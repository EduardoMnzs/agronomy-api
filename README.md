# Agronomy API

Backend do sistema Agronomy — RAG agrônomico sem vetores, baseado em raciocínio hierárquico sobre documentos técnicos (EMBRAPA, Jacto, laudos de solo, etc.).

## Funcionalidades

- Indexação de documentos: PDF, DOCX, CSV, XLSX, JSON
- Consultas em linguagem natural com citações rastreáveis (fonte + página)
- Base de conhecimento persistente gerenciada por admins
- Upload de documentos por consulta (TTL 24h)
- LLM multi-provider via LiteLLM (OpenAI, Azure, Gemini, Anthropic, etc.)
- Autenticação JWT (login + refresh token)

## Requisitos

- Python 3.11+
- PostgreSQL 16+
- [PageIndex](https://github.com/VectifyAI/PageIndex) instalado como dependência

## Instalação

```bash
# 1. Clone o repositório
git clone <url> agronomy-api
cd agronomy-api

# 2. Crie e ative o ambiente virtual
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 3. Instale o PageIndex (engine de indexação)
pip install -e ..\PageIndex   # caminho local durante dev
# ou: pip install git+https://github.com/VectifyAI/PageIndex.git

# 4. Instale as dependências
pip install -r requirements.txt

# 5. Configure o ambiente
cp .env.example .env
# Edite .env com suas chaves de API e configurações
```

## Banco de dados

```bash
# Sobe apenas o PostgreSQL via Docker
docker-compose up -d db

# Cria as tabelas e o primeiro usuário admin
python scripts/seed.py

# Customizado
python scripts/seed.py --email admin@empresa.com --name "Admin" --password "senha123"
```

## Rodando

```bash
uvicorn main:app --reload
```

API disponível em `http://localhost:8000`
Documentação interativa em `http://localhost:8000/docs`

## Docker (completo)

```bash
cp .env.example .env   # configure antes
docker-compose up --build
```

## Variáveis de ambiente

| Variável | Descrição | Padrão |
|---|---|---|
| `DATABASE_URL` | Connection string PostgreSQL | `postgresql://agronomy:agronomy@localhost:5432/agronomy` |
| `SECRET_KEY` | Chave JWT (troque em produção) | `change-me-in-production` |
| `LLM_MODEL` | Modelo LLM (prefixo LiteLLM) | `gpt-4o-2024-11-20` |
| `OPENAI_API_KEY` | Chave OpenAI | — |
| `AZURE_API_KEY` | Chave Azure OpenAI | — |
| `AZURE_API_BASE` | Endpoint Azure | — |
| `GEMINI_API_KEY` | Chave Google Gemini | — |
| `ANTHROPIC_API_KEY` | Chave Anthropic | — |

### Exemplos de `LLM_MODEL`

```
gpt-4o-2024-11-20
azure/gpt-4o-pageindex
gemini/gemini-1.5-pro
anthropic/claude-sonnet-4-6
```

## Endpoints

```
POST   /auth/login              Login (retorna access + refresh token)
POST   /auth/refresh            Renova access token

GET    /users/me                Usuário logado
GET    /users                   Lista usuários (admin)
POST   /users                   Cria usuário (admin)
PUT    /users/{id}              Atualiza usuário (admin)
DELETE /users/{id}              Remove usuário (admin)

GET    /knowledge               Lista base de conhecimento
POST   /knowledge               Indexa novo documento (admin)
DELETE /knowledge/{id}          Remove documento (admin)

GET    /documents               Documentos do usuário (sessão)
POST   /documents               Upload + indexação on-demand
DELETE /documents/{id}          Remove documento do usuário

POST   /query                   Consulta com citações
GET    /health                  Health check
```

### Exemplo de consulta

```bash
curl -X POST http://localhost:8000/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Qual a dose de calcário para meu solo?",
    "user_data": { "pH": 5.2, "V_percent": 38, "textura": "argilosa" }
  }'
```

```json
{
  "answer": "Para solo argiloso com pH 5.2 e V% 38, a dose recomendada é 2.8 t/ha [1]. O cálculo segue o método SMP [2]...",
  "sources": [
    { "ref": 1, "doc_name": "Manual EMBRAPA Calcário", "page": 42, "section": "Tabela 3.1" },
    { "ref": 2, "doc_name": "Manual EMBRAPA Calcário", "page": 38, "section": "Método SMP" }
  ],
  "model_used": "azure/gpt-4o-pageindex"
}
```

## Estrutura do projeto

```
agronomy-api/
├── api/
│   ├── deps.py              JWT dependency injection
│   └── routes/
│       ├── auth.py          /auth/login, /auth/refresh
│       ├── knowledge.py     /knowledge
│       ├── documents.py     /documents
│       ├── query.py         /query
│       └── users.py         /users
├── core/
│   ├── config.py            Settings (pydantic-settings)
│   ├── indexer.py           Integração PageIndex
│   ├── query_engine.py      Busca + citações + LLM
│   └── citations.py         Rastreamento de fontes
├── db/
│   ├── models.py            SQLAlchemy models
│   └── session.py           Engine + get_db()
├── parsers/
│   ├── base.py              Interface BaseParser
│   ├── pdf_parser.py
│   ├── docx_parser.py
│   ├── csv_parser.py
│   ├── xlsx_parser.py
│   ├── json_parser.py
│   └── factory.py           get_parser(file_path)
├── services/
│   └── auth.py              JWT + bcrypt
├── scripts/
│   └── seed.py              Cria usuário admin inicial
├── data/                    Arquivos e indexes (gitignored)
├── main.py                  FastAPI app
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
└── .env.example
```
