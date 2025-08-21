# Random.org MCP Server

这是一个基于 [Random.org JSON-RPC v4 API](https://api.random.org/json-rpc) 的 **MCP (Model Context Protocol) Server**，  
它将 Random.org 的真随机数服务封装为一系列可调用的工具（tools）和资源（resources）。

## ✨ 功能

本服务支持调用 Random.org 的常见随机方法，包括：

- `generateIntegers`：生成整数
- `generateIntegerSequences`：生成整数序列
- `generateDecimalFractions`：生成 [0,1) 区间的小数
- `generateGaussians`：生成高斯分布数
- `generateStrings`：生成随机字符串
- `generateUUIDs`：生成随机 UUID v4
- `generateBlobs`：生成随机二进制大对象 (BLOBs)
- `getUsage`：查询当前 API Key 的额度使用情况
- 健康检查工具 `health`  
- 示例集合资源 `examples_resource`

所有方法均严格按照 Random.org 文档进行参数校验，并对常见错误码提供友好提示。

## 🛠️ 安装与运行

### 1. 克隆或下载项目

```bash
git clone https://github.com/Bees-wind/mcp-randomorg.git
cd mcp-randomorg
```

### 2. 安装依赖

uv:

```
# 创建并激活虚拟环境
uv venv
source .venv/bin/activate  # Linux/macOS
# 或在 Windows 上使用:
# .venv\Scripts\activate
# 安装依赖
uv pip install -r requirements.txt
```

conda:

```
# 创建并激活虚拟环境
conda create -n randomorgmcp
conda activate randomorgmcp
# 安装依赖
pip install -r requirements.txt
```

### 3. 配置 API Key

在项目根目录/mcp-randomorg下创建 `.env` 文件，写入：

```
RANDOM_ORG_API_KEY=Your_random_org_api_key
```

RANDOM_ORG_API_KEY需要访问Random.org获取

### 4. 启动服务

```bash
python server.py
```

默认会通过 **stdio** 启动 MCP server，可直接对接 Claude Desktop / MCP Inspector 等 MCP 客户端。

__此项目仅支持**stdio**模式__

## 💻 客户端配置

使用uv：

```
{
  "mcpServers": {
    "mcp-randomorg": {
      "type": "stdio",
      "command": "uv",
      "args": [ "--directory",
                "/path/path", # 替换为你的目录
                "run",
                "server.py"],
      "env": {
        "RANDOM_ORG_API_KEY": "your_random_org_api_key"
      }
    }
  }
}
```

如果你使用conda，找到/envs下对应虚拟环境文件夹下的的python.exe，并在/mcp-randomorg创建一个bat：

```bat
@echo off
setlocal
cd /d path\to\randomorg-mcp
\path\to\python.exe -u server.py
```

```
{
  "mcpServers": {
    "mcp-randomorg": {
      "name": "@Bees-wind/mcp-randomorg",
      "type": "stdio",
      "description": "",
      "command": "/path/to/run.bat",# 替换为你的目录
      "args": [],
      "env": {
        "RANDOM_ORG_API_KEY": "your_random_org_api_key"
      }
    }
  }
},
```

如果之前配置了.env,这里RANDOM_ORG_API_KEY可以不写

## 📖 使用说明

- 每个工具方法对应 Random.org 的同名 API，输入参数与返回字段保持一致。
- 校验逻辑会在调用前拦截不合法参数，避免浪费额度。
- 出错时会返回人类友好的错误信息，例如额度不足、请求频率过高等。

## 📚 示例

本服务提供了一个内置资源：

```
randomorg://examples
```

可以获取常见调用的 JSON 示例，方便快速测试。

## 🔒 注意事项

- Random.org 有额度限制（bitsLeft/requestsLeft），请根据返回的提示控制调用频率。

## 声明

此项目包含由GPT-5生成的代码
