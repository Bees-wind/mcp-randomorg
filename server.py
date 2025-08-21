import os
import time
import uuid as _uuid
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession
from dotenv import load_dotenv   # 👈 新增

# === 基础配置 ===
RANDOM_ORG_ENDPOINT = "https://api.random.org/json-rpc/4/invoke"  # R4 基础API
API_KEY_ENV = "RANDOM_ORG_API_KEY"

# === 加载 .env 文件 ===
load_dotenv()   # 👈 自动读取项目根目录的 .env

mcp = FastMCP("Random.org MCP Server")


# === 友好错误：定义与映射 ===
class RandomOrgAPIError(RuntimeError):
    def __init__(self, code: Any, message: str, data: dict | None = None):
        super().__init__(f"Random.org 错误 {code}: {message}")
        self.code = code
        self.data = data or {}

def _map_random_org_error(code: Any, message: str, data: dict | None = None) -> str:
    """
    将 Random.org / 网络/HTTP 报错映射为更友好的提示与排查建议。
    """
    hints = {
        400: "请求参数不合法（400）。请检查 n/length/min/max/base 等是否在文档要求的范围内。",
        401: "认证失败（401）。请检查 RANDOM_ORG_API_KEY 是否正确、是否已在 .env 中加载。",
        402: "额度不足（402）。bitsLeft 或 requestsLeft 用尽，请更换/升级 API Key 或稍后再试。",
        403: "权限受限（403）。可能是 Key 权限不足或被限制来源。",
        413: "请求过大（413）。请减小 n 或 length 总和，或拆分请求。",
        429: "请求过于频繁（429）。请按照 advisoryDelay 做退避重试。",
        503: "服务暂不可用（503）。请稍后重试；可实现指数退避。",
        -32600: "JSON-RPC 请求格式错误（-32600）。请检查 method/params/id 字段。",
        -32601: "JSON-RPC 未知方法（-32601）。method 名称是否正确？",
        -32602: "JSON-RPC 参数非法（-32602）。请检查各字段类型与取值范围。",
        -32603: "JSON-RPC 内部错误（-32603）。请稍后重试或联系官方支持。",
    }
    # 默认兜底
    base_msg = hints.get(code, f"调用失败（代码：{code}）。原始信息：{message}")
    extra = []
    if data:
        # 常见诊断位
        if "advisoryDelay" in data:
            extra.append(f"建议等待 {data['advisoryDelay']} ms 后再试。")
        if "bitsLeft" in data:
            extra.append(f"当前 bitsLeft={data['bitsLeft']}。")
        if "requestsLeft" in data:
            extra.append(f"当前 requestsLeft={data['requestsLeft']}。")
    return " ".join([base_msg] + extra)

def _random_org_rpc(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"未检测到 API Key。请在项目根目录的 .env 中设置 {API_KEY_ENV}=你的密钥，并确保已 load_dotenv()。"
        )

    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": {"apiKey": api_key, **params},
        "id": int(time.time() * 1000),
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(RANDOM_ORG_ENDPOINT, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        # HTTP 层错误（非 JSON-RPC）
        friendly = _map_random_org_error(e.response.status_code, str(e))
        raise RuntimeError(friendly) from e
    except httpx.RequestError as e:
        # 网络层错误（超时/连接失败等）
        raise RuntimeError("网络请求失败：无法连接到 Random.org。请检查本机网络与防火墙。") from e
    except ValueError as e:
        # JSON 解析失败
        raise RuntimeError("响应解析失败：收到的不是合法的 JSON。") from e

    if "error" in data and data["error"]:
        err = data["error"]
        code = err.get("code")
        msg = err.get("message", "")
        err_data = err.get("data", {})
        friendly = _map_random_org_error(code, msg, err_data if isinstance(err_data, dict) else None)
        raise RuntimeError(friendly)

    return data["result"]

def _validate_pregenerated_randomization(pgr: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    允许:
      - None / 未提供
      - {"date": "YYYY-MM-DD"}  # ISO 8601，必须 <= 今天（UTC）
      - {"id": "PERSISTENT-IDENTIFIER"}  # 1~64 长度
    """
    if pgr is None:
        return None
    if not isinstance(pgr, dict):
        raise ValueError("pregeneratedRandomization 必须是对象或不提供")

    if "date" in pgr and "id" in pgr:
        raise ValueError("pregeneratedRandomization 不能同时包含 date 和 id")

    if "date" in pgr:
        val = pgr["date"]
        if not isinstance(val, str):
            raise ValueError("pregeneratedRandomization.date 必须是字符串（YYYY-MM-DD）")
        # 仅做基本格式检查；是否为过去/当天交由服务端进一步判定
        import re
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
            raise ValueError("pregeneratedRandomization.date 必须是 YYYY-MM-DD 格式")
        return {"date": val}

    if "id" in pgr:
        val = pgr["id"]
        if not isinstance(val, str):
            raise ValueError("pregeneratedRandomization.id 必须是字符串")
        if not (1 <= len(val) <= 64):
            raise ValueError("pregeneratedRandomization.id 长度必须在 1~64 之间")
        return {"id": val}

    raise ValueError("pregeneratedRandomization 需要包含 'date' 或 'id' 字段之一")

from typing import Any, Dict, List, Optional, Union, Tuple

ScalarOrListInt = Union[int, List[int]]
ScalarOrListBool = Union[bool, List[bool]]

def _ensure_scalar_or_list(
    name: str,
    value: ScalarOrListInt | ScalarOrListBool,
    n: int,
    scalar_validator=None,
    list_item_validator=None,
) -> Tuple[bool, List[Any] | int]:
    """
    统一判断该字段是标量（uniform）还是长度为 n 的列表（multiform）。
    返回 (is_scalar, normalized_value)，其中 normalized_value 为：
      - 若是标量：返回原标量
      - 若是列表：返回列表（长度==n）
    并对标量或每个列表元素调用 validator 做范围校验。
    """
    if isinstance(value, list):
        if len(value) != n:
            raise ValueError(f"{name} 为列表时，长度必须等于 n={n}")
        if list_item_validator:
            for i, v in enumerate(value):
                list_item_validator(v, f"{name}[{i}]")
        return False, value
    else:
        if scalar_validator:
            scalar_validator(value, name)
        return True, value

def _validate_int_range(val: int, name: str, lo: int, hi: int):
    if not (lo <= val <= hi):
        raise ValueError(f"{name} 必须在 [{lo}, {hi}] 范围内，当前为 {val}")

def _validate_bool(val: bool, name: str):
    if not isinstance(val, bool):
        raise ValueError(f"{name} 必须是布尔值 True/False")

def _validate_base(val: int, name: str):
    if val not in (2, 8, 10, 16):
        raise ValueError(f"{name} 只允许为 2、8、10、16")

def _validate_lengths_total(lengths: List[int]):
    total = sum(lengths)
    if not (1 <= total <= 10_000):
        raise ValueError(f"所有序列长度之和必须在 [1, 10000]，当前为 {total}")

def _validate_no_replacement_feasible(
    is_uniform: bool,
    length: ScalarOrListInt,
    min_value: ScalarOrListInt,
    max_value: ScalarOrListInt,
    replacement: ScalarOrListBool,
    n: int,
):
    """
    当 replacement=False（或对应条目为 False）时，需满足可抽取唯一值：
    max - min + 1 >= 对应序列长度
    """
    # 统一转成列表形式便于逐条判断
    if is_uniform:
        L = [length] * n
        MIN = [min_value] * n
        MAX = [max_value] * n
        R = [replacement] * n
    else:
        L = length  # type: ignore
        MIN = min_value  # type: ignore
        MAX = max_value  # type: ignore
        R = replacement  # type: ignore

    # 如果 replacement 是标量 True，直接过；否则逐条检查
    if isinstance(R, bool):
        if R is True:
            return
        # 全局不放回：需要对所有序列检查一次
        for i in range(n):
            domain = MAX[i] - MIN[i] + 1
            if domain < L[i]:
                raise ValueError(
                    f"replacement=False 时，第 {i} 个序列的可选数量 {domain} 小于长度 {L[i]}，无法无放回抽取"
                )
    else:
        # 每序列独立 replacement
        if len(R) != n:
            raise ValueError("replacement 为列表时长度必须等于 n")
        for i in range(n):
            if R[i] is False:
                domain = MAX[i] - MIN[i] + 1
                if domain < L[i]:
                    raise ValueError(
                        f"replacement[{i}]=False 时，可选数量 {domain} 小于长度 {L[i]}，无法无放回抽取"
                    )

# === 覆盖/更新后的 Tool: 生成真随机整数 ===
@mcp.tool(title="Generate True Random Integers")
def generate_integers(
    n: int,
    min_value: int,
    max_value: int,
    replacement: bool = True,
    base: int = 10,
    pregeneratedRandomization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    从 Random.org 获取 n 个 [min_value, max_value] 区间内的真随机整数。
    兼容可选参数:
      - replacement: 是否放回（默认 True）
      - base: 进制 2/8/10/16（默认 10）
      - pregeneratedRandomization: {"date":"YYYY-MM-DD"} 或 {"id":"..."}（或不提供）
    返回字段：values（整数/字符串列表，视 base 而定）, bitsUsed/bitsLeft/requestsLeft/advisoryDelay。
    """
    # —— 参数校验（与之前一致）——
    if not (1 <= n <= 10_000):
        raise ValueError("n 必须在 [1, 1e4] 范围内")
    if not (-1_000_000_000 <= min_value <= 1_000_000_000):
        raise ValueError("min 必须在 [-1e9, 1e9] 范围内")
    if not (-1_000_000_000 <= max_value <= 1_000_000_000):
        raise ValueError("max 必须在 [-1e9, 1e9] 范围内")
    if min_value > max_value:
        raise ValueError("min 不能大于 max")
    if base not in (2, 8, 10, 16):
        raise ValueError("base 只允许为 2、8、10、16")

    pgr = _validate_pregenerated_randomization(pregeneratedRandomization)

    params = {
        "n": n,
        "min": min_value,
        "max": max_value,
        "replacement": replacement,
        "base": base,
    }
    if pgr is not None:
        params["pregeneratedRandomization"] = pgr

    try:
        result = _random_org_rpc("generateIntegers", params)
    except RuntimeError as e:
        # 统一抛出更友好的消息（已在 _random_org_rpc 中映射过）
        raise

    data_values = result["random"]["data"]
    return {
        "values": data_values,
        "base": base,
        "replacement": replacement,
        "pregeneratedRandomization": pgr or None,
        "bitsUsed": result.get("bitsUsed"),
        "bitsLeft": result.get("bitsLeft"),
        "requestsLeft": result.get("requestsLeft"),
        "advisoryDelay": result.get("advisoryDelay"),
    }

@mcp.tool(title="Generate Integer Sequences")
def generate_integer_sequences(
    n: int,
    length: ScalarOrListInt,
    min_value: ScalarOrListInt,
    max_value: ScalarOrListInt,
    replacement: ScalarOrListBool = True,
    base: ScalarOrListInt = 10,
    pregeneratedRandomization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    生成 n 组整数序列（支持 uniform / multiform）：
      - uniform：length/min/max/replacement/base 为标量
      - multiform：上述字段为长度 n 的列表（base 值限定在 {2,8,10,16}）
    返回字段：sequences（二维列表；当 base!=10 时内部元素为字符串）
            以及 bitsUsed/bitsLeft/requestsLeft/advisoryDelay。
    """
    # —— 必填参数校验 —— 
    _validate_int_range(n, "n", 1, 1000)


    is_len_scalar, length_v = _ensure_scalar_or_list(
        "length", length, n,
        scalar_validator=lambda v, nm: _validate_int_range(v, nm, 1, 10_000),
        list_item_validator=lambda v, nm: _validate_int_range(v, nm, 1, 10_000),
    )
    if is_len_scalar:
        total = n * int(length_v)
        if not (1 <= total <= 10_000):
            raise ValueError(f"所有序列长度之和必须在 [1, 10000]，当前为 {total}")
    else:
        _validate_lengths_total(length_v)  # type: ignore

    is_min_scalar, min_v = _ensure_scalar_or_list(
        "min", min_value, n,
        scalar_validator=lambda v, nm: _validate_int_range(v, nm, -1_000_000_000, 1_000_000_000),
        list_item_validator=lambda v, nm: _validate_int_range(v, nm, -1_000_000_000, 1_000_000_000),
    )
    is_max_scalar, max_v = _ensure_scalar_or_list(
        "max", max_value, n,
        scalar_validator=lambda v, nm: _validate_int_range(v, nm, -1_000_000_000, 1_000_000_000),
        list_item_validator=lambda v, nm: _validate_int_range(v, nm, -1_000_000_000, 1_000_000_000),
    )

    def _as_list(val, is_scalar):
        return [val] * n if is_scalar else val

    min_list = _as_list(min_v, is_min_scalar)
    max_list = _as_list(max_v, is_max_scalar)
    for i, (mn, mx) in enumerate(zip(min_list, max_list)):
        if mn > mx:
            raise ValueError(f"第 {i} 个序列的 min 不能大于 max（{mn} > {mx}）")

    is_rep_scalar, rep_v = _ensure_scalar_or_list(
        "replacement", replacement, n,
        scalar_validator=_validate_bool,
        list_item_validator=_validate_bool,
    )
    is_base_scalar, base_v = _ensure_scalar_or_list(
        "base", base, n,
        scalar_validator=_validate_base,
        list_item_validator=_validate_base,
    )

    pgr = _validate_pregenerated_randomization(pregeneratedRandomization)

    _validate_no_replacement_feasible(
        is_len_scalar, length_v, min_v, max_v, rep_v, n
    )

    params: Dict[str, Any] = {
        "n": n,
        "length": length_v,
        "min": min_v,
        "max": max_v,
        "replacement": rep_v,
        "base": base_v,
    }
    if pgr is not None:
        params["pregeneratedRandomization"] = pgr

    try:
        result = _random_org_rpc("generateIntegerSequences", params)
    except RuntimeError as e:
        # 统一抛出更友好的消息（已在 _random_org_rpc 中映射过）
        raise

    return {
        "sequences": result["random"]["data"],
        "base": base_v,
        "replacement": rep_v,
        "pregeneratedRandomization": pgr or None,
        "bitsUsed": result.get("bitsUsed"),
        "bitsLeft": result.get("bitsLeft"),
        "requestsLeft": result.get("requestsLeft"),
        "advisoryDelay": result.get("advisoryDelay"),
    }

# === Tool: 生成真随机小数（[0,1) 区间） ===
@mcp.tool(title="Generate True Random Decimal Fractions")
def generate_decimal_fractions(
    n: int,
    decimalPlaces: int,
    replacement: bool = True,
    pregeneratedRandomization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    从 Random.org 获取 n 个位于 [0,1) 的真随机小数，保留 decimalPlaces 位小数。
    可选：
      - replacement: 是否放回（默认 True）
      - pregeneratedRandomization: {"date":"YYYY-MM-DD"} 或 {"id":"..."}（或不提供）
    返回字段：fractions（字符串列表，例如 "0.123456"），以及配额信息。
    """
    # —— 参数校验 —— 
    _validate_int_range(n, "n", 1, 10_000)
    _validate_int_range(decimalPlaces, "decimalPlaces", 1, 14)
    _validate_bool(replacement, "replacement")
    pgr = _validate_pregenerated_randomization(pregeneratedRandomization)

    params: Dict[str, Any] = {
        "n": n,
        "decimalPlaces": decimalPlaces,
        "replacement": replacement,
    }
    if pgr is not None:
        params["pregeneratedRandomization"] = pgr

    # —— 调用 Random.org JSON-RPC —— 
    result = _random_org_rpc("generateDecimalFractions", params)

    # Random.org 返回字符串形式的小数数据
    data_values = result["random"]["data"]
    return {
        "fractions": data_values,  # e.g. ["0.1234", "0.9876", ...]
        "decimalPlaces": decimalPlaces,
        "replacement": replacement,
        "pregeneratedRandomization": pgr or None,
        "bitsUsed": result.get("bitsUsed"),
        "bitsLeft": result.get("bitsLeft"),
        "requestsLeft": result.get("requestsLeft"),
        "advisoryDelay": result.get("advisoryDelay"),
    }

# === Tool: 生成真随机高斯分布数 ===
@mcp.tool(title="Generate True Random Gaussians")
def generate_gaussians(
    n: int,
    mean: float,
    standardDeviation: float,
    significantDigits: int,
    pregeneratedRandomization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    从 Random.org 获取 n 个来自高斯（正态）分布的真随机数。
    参数：
      - n ∈ [1, 10000]
      - mean ∈ [-1e6, 1e6]
      - standardDeviation ∈ [-1e6, 1e6]   # 按官方范围约束；若需强制 >0 可在此加额外限制
      - significantDigits ∈ [2, 14]
      - pregeneratedRandomization: {"date": "YYYY-MM-DD"} 或 {"id": "..."}（或不提供）
    说明：高斯分布结果总是“有放回”抽取。
    返回：values（浮点字符串列表），以及配额信息。
    """
    # —— 参数校验 —— 
    _validate_int_range(n, "n", 1, 10_000)
    # 这里沿用文档给定范围（不强制 sigma>0；如需可自行加：if standardDeviation <= 0: ...）
    if not (-1_000_000 <= mean <= 1_000_000):
        raise ValueError("mean 必须在 [-1e6, 1e6] 范围内")
    if not (-1_000_000 <= standardDeviation <= 1_000_000):
        raise ValueError("standardDeviation 必须在 [-1e6, 1e6] 范围内")
    _validate_int_range(significantDigits, "significantDigits", 2, 14)

    pgr = _validate_pregenerated_randomization(pregeneratedRandomization)

    params: Dict[str, Any] = {
        "n": n,
        "mean": mean,
        "standardDeviation": standardDeviation,
        "significantDigits": significantDigits,
    }
    if pgr is not None:
        params["pregeneratedRandomization"] = pgr

    # —— 调用 Random.org JSON-RPC —— 
    result = _random_org_rpc("generateGaussians", params)

    # Random.org 返回字符串形式的数值
    data_values = result["random"]["data"]
    return {
        "values": data_values,
        "mean": mean,
        "standardDeviation": standardDeviation,
        "significantDigits": significantDigits,
        "pregeneratedRandomization": pgr or None,
        "bitsUsed": result.get("bitsUsed"),
        "bitsLeft": result.get("bitsLeft"),
        "requestsLeft": result.get("requestsLeft"),
        "advisoryDelay": result.get("advisoryDelay"),
    }

# === Tool: 生成真随机字符串 ===
@mcp.tool(title="Generate True Random Strings")
def generate_strings(
    n: int,
    length: int,
    characters: str,
    replacement: bool = True,
    pregeneratedRandomization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    从 Random.org 获取 n 个真随机字符串。
    必填：
      - n ∈ [1, 10000]
      - length ∈ [1, 32]   （每个字符串长度）
      - characters: 允许使用的字符集合字符串（长度 1..128）
    可选：
      - replacement: 是否放回（默认 True；False 则要求结果互不相同）
      - pregeneratedRandomization: {"date":"YYYY-MM-DD"} 或 {"id":"..."}（或不提供）
    返回：strings（字符串列表），以及配额信息。
    """
    # —— 参数校验 ——
    _validate_int_range(n, "n", 1, 10_000)
    _validate_int_range(length, "length", 1, 32)
    if not isinstance(characters, str):
        raise TypeError("characters 必须是字符串")
    if not (1 <= len(characters) <= 128):
        raise ValueError("characters 的长度必须在 [1, 128] 范围内")
    _validate_bool(replacement, "replacement")
    pgr = _validate_pregenerated_randomization(pregeneratedRandomization)

    params: Dict[str, Any] = {
        "n": n,
        "length": length,
        "characters": characters,
        "replacement": replacement,
    }
    if pgr is not None:
        params["pregeneratedRandomization"] = pgr

    # —— 调用 Random.org JSON-RPC ——
    result = _random_org_rpc("generateStrings", params)

    data_values = result["random"]["data"]  # List[str]
    return {
        "strings": data_values,
        "length": length,
        "characters": characters,
        "replacement": replacement,
        "pregeneratedRandomization": pgr or None,
        "bitsUsed": result.get("bitsUsed"),
        "bitsLeft": result.get("bitsLeft"),
        "requestsLeft": result.get("requestsLeft"),
        "advisoryDelay": result.get("advisoryDelay"),
    }

# === Tool: 生成真随机 UUID v4（RFC 4122 §4.4） ===
@mcp.tool(title="Generate True Random UUIDv4")
def generate_uuids(
    n: int,
    pregeneratedRandomization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    从 Random.org 获取 n 个真随机 UUID v4。
    参数：
      - n ∈ [1, 1000]
      - pregeneratedRandomization: 可不提供，或 {"date":"YYYY-MM-DD"}，或 {"id":"..."}（长度 1..64）
    返回：
      - uuids: List[str]，例如 ["550e8400-e29b-41d4-a716-446655440000", ...]
      - 以及配额与速率相关字段（bitsUsed/bitsLeft/requestsLeft/advisoryDelay）
    """
    # —— 参数校验 ——
    _validate_int_range(n, "n", 1, 1000)
    pgr = _validate_pregenerated_randomization(pregeneratedRandomization)

    params: Dict[str, Any] = {"n": n}
    if pgr is not None:
        params["pregeneratedRandomization"] = pgr

    # —— 调用 Random.org JSON-RPC ——
    result = _random_org_rpc("generateUUIDs", params)

    data_values = result["random"]["data"]  # List[str]
    return {
        "uuids": data_values,
        "pregeneratedRandomization": pgr or None,
        "bitsUsed": result.get("bitsUsed"),
        "bitsLeft": result.get("bitsLeft"),
        "requestsLeft": result.get("requestsLeft"),
        "advisoryDelay": result.get("advisoryDelay"),
    }

# === Tool: 生成真随机 BLOB（二进制大对象） ===
@mcp.tool(title="Generate True Random Blobs")
def generate_blobs(
    n: int,
    size: int,
    format: str = "base64",
    pregeneratedRandomization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    从 Random.org 获取 n 个真随机 BLOB（二进制大对象）。
    参数：
      - n ∈ [1, 100]                 # BLOB 个数
      - size ∈ [1, 1048576] 且能被 8 整除   # 每个 BLOB 的位数
      - n * size ≤ 1048576 (128 KiB) # 总大小限制
      - format: "base64" (默认) 或 "hex"
      - pregeneratedRandomization: {"date":"YYYY-MM-DD"} 或 {"id":"..."}（或不提供）
    返回：
      - blobs: List[str]，每个为随机 BLOB 的字符串表示
      - 以及配额信息（bitsUsed/bitsLeft/requestsLeft/advisoryDelay）
    """
    # —— 参数校验 ——
    _validate_int_range(n, "n", 1, 100)
    _validate_int_range(size, "size", 1, 1_048_576)
    if size % 8 != 0:
        raise ValueError("size 必须能被 8 整除（按位数计数）")
    if n * size > 1_048_576:
        raise ValueError("总请求大小 n*size 不得超过 1,048,576 bits (128 KiB)")
    if format not in ("base64", "hex"):
        raise ValueError("format 必须是 'base64' 或 'hex'")

    pgr = _validate_pregenerated_randomization(pregeneratedRandomization)

    params: Dict[str, Any] = {
        "n": n,
        "size": size,
        "format": format,
    }
    if pgr is not None:
        params["pregeneratedRandomization"] = pgr

    # —— 调用 Random.org JSON-RPC ——
    result = _random_org_rpc("generateBlobs", params)

    data_values = result["random"]["data"]  # List[str]
    return {
        "blobs": data_values,
        "size": size,
        "format": format,
        "pregeneratedRandomization": pgr or None,
        "bitsUsed": result.get("bitsUsed"),
        "bitsLeft": result.get("bitsLeft"),
        "requestsLeft": result.get("requestsLeft"),
        "advisoryDelay": result.get("advisoryDelay"),
    }

# === Tool: 查询账户用量（剩余请求、剩余比特等） ===
@mcp.tool(title="Get Random.org Usage")
def get_usage() -> Dict[str, Any]:
    """
    查询当前 API Key 的用量信息（剩余 bits、剩余请求数、总使用等）。
    """
    result = _random_org_rpc("getUsage", {})
    return result  # 已包含 bitsLeft / requestsLeft 等字段

# === Resource（可选）：以资源的形式暴露用量 ===
@mcp.resource("randomorg://usage")
def usage_resource() -> str:
    """以只读资源的形式返回用量 JSON 字符串。"""
    import json
    return json.dumps(get_usage(), ensure_ascii=False, indent=2)

# === 进度/日志示例（可选）：演示 ctx 的使用 ===
@mcp.tool(title="Health Check")
def health(ctx: Context[ServerSession, None]) -> Dict[str, str]:
    """
    简单存活检查，演示如何在 MCP 日志面板输出信息。
    """
    ctx.info("Random.org MCP Server is healthy ✅")
    return {"status": "ok"}

@mcp.resource("randomorg://examples")
def examples_resource() -> str:
    import json
    examples = {
        "generate_blobs": [
            {
                "title": "基本示例：生成 2 个，每个 256 位（32 字节），默认 base64",
                "arguments": { "n": 2, "size": 256 }
            },
            {
                "title": "Hex 编码的随机 BLOB",
                "arguments": { "n": 1, "size": 512, "format": "hex" }
            },
            {
                "title": "历史随机化（按日期可重放）",
                "arguments": { "n": 3, "size": 128, "pregeneratedRandomization": { "date": "2024-06-01" } }
            },
            {
                "title": "历史随机化（自定义 ID 可共享）",
                "arguments": { "n": 1, "size": 1024, "pregeneratedRandomization": { "id": "PUBLIC-BLOBS-DEMO" } }
            }
        ],

            "generate_uuids": [
            {
                "title": "基本示例：生成 5 个 UUIDv4",
                "arguments": { "n": 5 }
            },
            {
                "title": "历史随机化（按日期可重放）",
                "arguments": { "n": 3, "pregeneratedRandomization": { "date": "2024-06-01" } }
            },
            {
                "title": "历史随机化（自定义 ID 可共享）",
                "arguments": { "n": 3, "pregeneratedRandomization": { "id": "PUBLIC-UUIDS-DEMO" } }
            }
        ],

            "generate_strings": [
            {
                "title": "基本示例：长度 12，可重复，从字母数字集中采样",
                "arguments": {
                    "n": 5,
                    "length": 12,
                    "characters": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
                }
            },
            {
                "title": "不可重复（如抽奖券场景）",
                "arguments": {
                    "n": 10,
                    "length": 8,
                    "characters": "ABCDEFGHJKLMNPQRSTUVWXYZ23456789",
                    "replacement": False
                }
            },
            {
                "title": "历史随机化（按日期可重放）",
                "arguments": {
                    "n": 3,
                    "length": 16,
                    "characters": "abcdef0123456789",
                    "pregeneratedRandomization": { "date": "2024-06-01" }
                }
            },
            {
                "title": "历史随机化（自定义 ID 可共享）",
                "arguments": {
                    "n": 3,
                    "length": 20,
                    "characters": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    "pregeneratedRandomization": { "id": "PUBLIC-STRINGS-DEMO" }
                }
            }
        ],

        "generate_gaussians": [
            {
                "title": "基本示例：N(0, 1) ，保留 6 位有效数字",
                "arguments": { "n": 5, "mean": 0, "standardDeviation": 1, "significantDigits": 6 }
            },
            {
                "title": "自定义均值/方差，历史随机化（按日期可重放）",
                "arguments": { "n": 10, "mean": 100.5, "standardDeviation": 20, "significantDigits": 8,
                               "pregeneratedRandomization": { "date": "2024-06-01" } }
            },
            {
                "title": "历史随机化（自定义 ID 可共享）",
                "arguments": { "n": 3, "mean": -3.2, "standardDeviation": 0.75, "significantDigits": 10,
                               "pregeneratedRandomization": { "id": "PUBLIC-GAUSS-DEMO" } }
            }
        ],

        "generate_decimal_fractions": [
            {
                "title": "基本示例（4 位小数，可重复）",
                "arguments": { "n": 5, "decimalPlaces": 4 }
            },
            {
                "title": "不可重复（相同小数不会重复出现）",
                "arguments": { "n": 10, "decimalPlaces": 6, "replacement": False }
            },
            {
                "title": "历史随机化（按日期可重放）",
                "arguments": { "n": 3, "decimalPlaces": 8, "pregeneratedRandomization": { "date": "2024-06-01" } }
            },
            {
                "title": "历史随机化（自定义 ID 可共享）",
                "arguments": { "n": 3, "decimalPlaces": 8, "pregeneratedRandomization": { "id": "PUBLIC-FRACTION-DEMO" } }
            }
        ],
        "generate_integers": [
            {
                "title": "基本示例（十进制，可重复）",
                "arguments": { "n": 5, "min_value": 1, "max_value": 6 }
            },
            {
                "title": "十六进制、不可重复",
                "arguments": { "n": 8, "min_value": 0, "max_value": 255, "replacement": False, "base": 16 }
            },
            {
                "title": "历史随机化（按日期可重放）",
                "arguments": { "n": 4, "min_value": 1, "max_value": 100, "pregeneratedRandomization": { "date": "2024-01-01" } }
            },
            {
                "title": "历史随机化（自定义 ID 可共享）",
                "arguments": { "n": 4, "min_value": 1, "max_value": 100, "pregeneratedRandomization": { "id": "PUBLIC-DRAW-EXAMPLE" } }
            }
        ],
        "generate_integer_sequences": [
            {
                "title": "Uniform：3 组 × 5 个，范围 1..6",
                "arguments": { "n": 3, "length": 5, "min_value": 1, "max_value": 6, "replacement": True, "base": 10 }
            },
            {
                "title": "Multiform：每组不同参数",
                "arguments": {
                    "n": 3,
                    "length": [4, 6, 3],
                    "min_value": [0, 100, -5],
                    "max_value": [7, 105, 5],
                    "replacement": [False, True, False],
                    "base": [2, 10, 16]
                }
            },
            {
                "title": "Multiform + 历史随机化（ID）",
                "arguments": {
                    "n": 2,
                    "length": [3, 3],
                    "min_value": [1, 50],
                    "max_value": [10, 60],
                    "replacement": [False, True],
                    "base": [10, 10],
                    "pregeneratedRandomization": { "id": "PUBLIC-DRAW-2025-08-19" }
                }
            }
        ]
    }
    return json.dumps(examples, ensure_ascii=False, indent=2)

def main():
    # 直接以 stdio 运行（适配 Claude Desktop / MCP Inspector）
    mcp.run()

if __name__ == "__main__":
    main()
