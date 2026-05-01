# CLAUDE.md

Instruções para o Claude Code ao trabalhar neste repositório.

## Visão geral

**Agronomy API** é o backend de um sistema RAG agrônomico sem vetores. Indexa documentos técnicos (EMBRAPA, Jacto, laudos de solo, etc.) usando o engine [PageIndex](../PageIndex) e responde consultas em linguagem natural com citações rastreáveis (fonte + página).

## Comandos comuns

```bash
# Subir o banco de dados
docker-compose up -d db

# Rodar a API em modo desenvolvimento
uvicorn main:app --reload

# Criar usuário admin inicial
python scripts/seed.py

# Instalar PageIndex como dependência local
pip install -e ..\PageIndex
```

Não há testes automatizados ainda. Validação via `/docs` (Swagger) após subir a API.

## Arquitetura

### Pipeline de indexação

```
Arquivo (PDF/DOCX/CSV/XLSX/JSON)
    → parsers/factory.py  (get_parser por extensão)
    → Parser específico   (texto + page_map)
    → core/indexer.py
         ├── PDF: page_index_main() diretamente
         └── Outros: salva .md temporário → md_to_tree()
    → JSON de estrutura salvo em data/*/indexes/
    → Registro em PostgreSQL
```

### Pipeline de consulta

```
POST /query
    → Carrega índices dos documentos selecionados
    → core/query_engine.py
         ├── _identify_nodes(): LLM identifica nós relevantes por documento
         ├── _extract_node_text(): extrai texto dos nós identificados
         ├── build_context_block(): formata contexto numerado [1][2]...
         └── LLM gera resposta com citações inline
    → extract_sources(): mapeia [N] → { doc, página, seção }
    → Retorna { answer, sources[], model_used }
```

### Arquivos críticos

| Arquivo | Responsabilidade |
|---|---|
| `core/indexer.py` | Integração PageIndex — PDF direto, outros via .md temporário |
| `core/query_engine.py` | Busca multi-documento, prompt agrônomico, citações |
| `core/citations.py` | `build_context_block` e `extract_sources` |
| `parsers/factory.py` | `get_parser(file_path)` — roteamento por extensão |
| `db/models.py` | `User`, `KnowledgeDocument`, `SessionDocument` |
| `api/deps.py` | `get_current_user`, `require_admin` — JWT middleware |
| `core/config.py` | Todas as settings via pydantic-settings + `.env` |

## Configuração

**`.env`** (copiar de `.env.example`). Variáveis principais:

- `DATABASE_URL` — PostgreSQL connection string
- `SECRET_KEY` — chave JWT (obrigatório trocar em produção)
- `LLM_MODEL` — prefixo LiteLLM, ex: `azure/gpt-4o-pageindex`, `gemini/gemini-1.5-pro`
- Chaves de API: `OPENAI_API_KEY`, `AZURE_API_KEY` + `AZURE_API_BASE`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`

**Precedência de LLM:** o campo `model` no body do `POST /query` sobrescreve `LLM_MODEL` do `.env`.

## Roles e permissões

- `admin` — acesso total: gerenciar usuários, indexar base de conhecimento
- `user` — consultas, upload de documentos de sessão (TTL 24h)

Proteção via `Depends(require_admin)` ou `Depends(get_current_user)` nas rotas.

## Convenções

- Respostas de erro em português (mensagem no campo `detail`)
- Datas sempre em ISO 8601 nas respostas JSON
- Documentos da base de conhecimento: `data/knowledge/` (permanente)
- Documentos de sessão: `data/sessions/{user_id}/` (TTL 24h, não gitignored)
- Nunca commitar `data/` nem `.env`

## Formatos suportados

| Extensão | Parser | Estratégia |
|---|---|---|
| `.pdf` | pymupdf | Texto por página — vai direto ao PageIndex |
| `.docx` | python-docx | Parágrafos + tabelas → Markdown, seções como páginas |
| `.csv` | pandas | Markdown tabular (max 500 linhas) |
| `.xlsx` / `.xls` | openpyxl + pandas | Uma aba = uma página |
| `.json` | built-in | Chaves como títulos, valores como bullets |

Para adicionar novo formato: criar `parsers/novo_parser.py` herdando `BaseParser` e registrar em `parsers/factory.py`.

## Dependência PageIndex

O PageIndex é instalado como pacote local:
```bash
pip install -e ..\PageIndex
```

Funções usadas:
- `pageindex.page_index_main(pdf_path, opt)` — indexação de PDF
- `pageindex.page_index_md.md_to_tree(md_path, ...)` — indexação de Markdown (async)
- `pageindex.utils.create_node_mapping(structure)` — monta dict node_id → node
- `pageindex.utils.extract_json(raw)` — extrai JSON de resposta LLM
- `pageindex.utils.ConfigLoader().load(opts)` — carrega config com defaults
