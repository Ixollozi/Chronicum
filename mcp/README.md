# Chronicum MCP

Коннектор к единой базе знаний: нейронки спрашивают Chronicum по MCP, а не читают правила/скиллы из папок проекта.

Нужен запущенный API: `http://127.0.0.1:8000` (lite: uvicorn в `backend`). HTTP MCP смонтирован на том же процессе: `http://127.0.0.1:8000/mcp`. В кабинете: Настройки → IDE → «Добавить в Cursor».

## Установка

```powershell
cd D:\Cursor\Chronicum
.\.venv\Scripts\pip.exe install -e mcp
```

Токен тот же, что у веба: `CHRONICUM_TOKEN` или `WEB_API_TOKEN` (по умолчанию `chronicum-dev-token`).

## Tools

| Tool | Назначение |
|---|---|
| `health` | API жив, `mode`, `ai_ready` |
| `search` | вопрос к базе + карточки источников |
| `list_materials` | фильтры `q` / `category` / `tag` / `status` |
| `get_material` | `level`: `summary` · `card` · `full` (`raw_text` только в `full`) |
| `list_taxonomy` | категории и теги — не выдумывать имена |
| `add_note` | положить правило/скилл/мысль в базу |
| `add_url` | ссылка в очередь разбора |

Скиллы и правила клади заметкой в Chronicum (`add_note`). В репозитории проекта достаточно MCP.

## Cursor (stdio)

Скопируй [`cursor.mcp.json.example`](cursor.mcp.json.example) в пользовательский MCP-конфиг Cursor (не коммить токен). На Windows это обычно `%USERPROFILE%\.cursor\mcp.json`.

```json
{
  "mcpServers": {
    "chronicum": {
      "command": "D:\\Cursor\\Chronicum\\.venv\\Scripts\\python.exe",
      "args": ["-m", "chronicum_mcp"],
      "env": {
        "CHRONICUM_API_URL": "http://127.0.0.1:8000",
        "CHRONICUM_TOKEN": "chronicum-dev-token"
      }
    }
  }
}
```

Проверка: API должен отвечать на `GET http://127.0.0.1:8000/health`. В Cursor перезапусти MCP `chronicum`.

## Claude Desktop (stdio)

Пример: [`claude_desktop_config.example.json`](claude_desktop_config.example.json). Файл конфига Claude Desktop:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Тот же `command` / `args` / `env`, что у Cursor.

## HTTP (любая нейронка без stdio)

```powershell
.\.venv\Scripts\python.exe -m chronicum_mcp --http --port 8001
```

Endpoint: `http://127.0.0.1:8001/mcp`

Если задан реальный токен (не пустой / `replace_me` / `changeme`), HTTP требует `Authorization: Bearer <token>`.

## Переменные

| Env | Смысл |
|---|---|
| `CHRONICUM_API_URL` | база API, default `http://127.0.0.1:8000` |
| `CHRONICUM_TOKEN` | Bearer к API (и к HTTP MCP) |
| `WEB_API_TOKEN` | запасной, если `CHRONICUM_TOKEN` не задан |

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest mcp/tests -q
```
