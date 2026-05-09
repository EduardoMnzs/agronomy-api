# Agronomy API

Backend do sistema **Agronomy** — RAG agrônomico sem vetores, baseado em raciocínio
hierárquico sobre documentos técnicos (EMBRAPA, Jacto, laudos de solo, etc.) usando
o engine [PageIndex](https://github.com/VectifyAI/PageIndex).

Respostas em linguagem natural com **citações rastreáveis** (documento + página + seção)
e suporte multi-LLM via [LiteLLM](https://github.com/BerriAI/litellm).

---

## Sumário

- [Funcionalidades](#funcionalidades)
- [Stack](#stack)
- [Setup local (sem Docker)](#setup-local-sem-docker)
- [Setup com Docker](#setup-com-docker)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Endpoints](#endpoints)
- [Segurança](#segurança)
- [Migrations (Alembic)](#migrations-alembic)
- [Deploy em produção](#deploy-em-produção)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Documentação adicional](#documentação-adicional)

---

## Funcionalidades

- Indexação de documentos: **PDF, DOCX, CSV, XLSX, JSON, MD**
- Consultas em linguagem natural com **citações inline rastreáveis**
- Base de conhecimento persistente (admin) + uploads de sessão (TTL 24h) + documentos pessoais
- LLM multi-provider via LiteLLM (OpenAI, Azure OpenAI, Gemini, Anthropic)
- Autenticação **JWT** (access + refresh) com expiração obrigatória
- Pedidos públicos de acesso com aprovação por admin
- Reset de senha por email (Resend)
- Indexação assíncrona via worker ARQ (Redis)
- **Storage abstrato**: modo `local` (dev) ou **AWS S3** (produção) — stateless, sem disco persistente
- Rate-limiting (slowapi) em endpoints sensíveis
- Security headers, parser hardening (anti zip-bomb / billion-laughs / CSV-bomb)
- Healthcheck distinto para liveness e readiness

## Stack

| Camada | Tecnologia |
|---|---|
| Web | FastAPI + Uvicorn + Gunicorn |
| ORM / Migrations | SQLAlchemy 2 + Alembic |
| DB / Cache / Queue | PostgreSQL 16 + Redis 7 + ARQ |
| LLM | LiteLLM (OpenAI / Azure / Gemini / Anthropic) |
| Indexação | PageIndex |
| Parsers | pymupdf (PDF), python-docx, openpyxl, pandas, defusedxml |
| Storage | Local (dev) · AWS S3 / S3-compatible (produção) via `boto3` |
| Cache de índices | `cachetools` TTLCache in-process (5 min) |
| Email | Resend |
| Reverse proxy / TLS | Caddy 2 (Let's Encrypt automático) |
| Container | Docker multi-stage com `uv` (Astral) |
| Build | `uv` no builder, `gunicorn + UvicornWorker` no runtime |

## Requisitos

- Docker + Docker Compose v2 (recomendado), **OU**
- Python 3.11+, PostgreSQL 16+ e Redis 7+ rodando localmente
- [PageIndex](https://github.com/VectifyAI/PageIndex) (instalável como pacote local)

---

## Setup local (sem Docker)

```powershell
# 1. Clone
git clone <url> agronomy-api
cd agronomy-api

# 2. Ambiente virtual
py -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/macOS

# 3. PageIndex (engine de indexação) — caminho local em dev
pip install -e ..\PageIndex

# 4. Dependências
pip install -r requirements.txt

# 5. Configuração
cp .env.example .env
#   Edite .env: gere SECRET_KEY forte, configure DB/Redis passwords e chaves de LLM
#   SECRET_KEY: py -c "import secrets; print(secrets.token_urlsafe(64))"

# 6. Suba Postgres + Redis (override dev expõe portas no localhost)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db redis

# 7. Aplica migrations
py -m alembic upgrade head

# 8. Cria o primeiro admin (senha forte gerada se omitida)
py scripts/seed.py --email admin@empresa.com --name "Administrador"

# 9. Sobe a API
py -m uvicorn main:app --host 127.0.0.1 --port 8000
```

API em `http://127.0.0.1:8000`. Documentação Swagger só com `DEBUG=true` no `.env`
(em `/docs` e `/redoc`).

## Setup com Docker

### Desenvolvimento (sem Caddy/TLS)

```powershell
cp .env.example .env
# Edite .env (mínimo: SECRET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD)

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

API exposta em `http://127.0.0.1:8000`. DB em `127.0.0.1:5433`, Redis em `127.0.0.1:6379`.

### Produção (com Caddy + TLS automático)

```bash
# .env precisa ter PUBLIC_DOMAIN apontando pro hostname público
# e ACME_EMAIL para o Let's Encrypt
docker compose up -d --build
```

Caddy expõe **80/443** com cert Let's Encrypt automático para `${PUBLIC_DOMAIN}`.
A API e o worker ficam em rede interna, sem porta exposta no host. Postgres e Redis
idem — só acessíveis via `docker exec` ou pela rede interna do compose.

---

## Variáveis de ambiente

Veja `.env.example` para o template completo. Variáveis críticas:

### Aplicação

| Variável | Descrição | Default |
|---|---|---|
| `APP_NAME` | Nome da aplicação | `Agronomy API` |
| `DEBUG` | `true` libera `/docs`, relaxa validação de `SECRET_KEY` | `false` |
| `SECRET_KEY` | Chave JWT (≥64 chars random). **Boot falha em prod com default** | — |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | TTL do access token (cap 1440) | `60` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | TTL do refresh token (cap 30) | `7` |
| `REMEMBER_ME_DAYS` | TTL para sessões com `remember_me=true` | `30` |
| `ALLOWED_ORIGINS` | Lista CORS separada por vírgula. **Sem `*`** | `http://localhost:5173,http://localhost:3000` |

### Banco / Cache / Worker

| Variável | Descrição |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Credenciais Postgres |
| `REDIS_PASSWORD` | Senha Redis |
| `DATABASE_URL` | Connection string Postgres |
| `REDIS_URL` | Connection string Redis (com senha) |

### LLM

| Variável | Descrição |
|---|---|
| `LLM_MODEL` | Modelo default (prefixo LiteLLM) |
| `INDEX_MODEL` / `QUERY_MODEL` / `ROUTER_MODEL` / `AGENT_MODEL` | Override por estágio (opcional) |
| `OPENAI_API_KEY` / `AZURE_API_KEY` / `AZURE_API_BASE` / `AZURE_API_VERSION` | Provider OpenAI/Azure |
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` | Outros providers |
| `ALLOWED_LLM_MODELS` | Allowlist do param `model` em `/query`. Vazio = só admins escolhem |

### Email (Resend)

| Variável | Descrição |
|---|---|
| `RESEND_API_KEY` | API key do Resend (vazio → emails só logados) |
| `FROM_EMAIL` | Remetente |
| `APP_BASE_URL` | URL pública do frontend (usada em links de reset de senha) |

### Storage (S3)

| Variável | Descrição | Default |
|---|---|---|
| `STORAGE_BACKEND` | `local` (dev) ou `s3` (produção) | `local` |
| `S3_BUCKET` | Nome do bucket S3 | — |
| `S3_REGION` | Região AWS (ex: `sa-east-1`) | `us-east-1` |
| `S3_ACCESS_KEY` | Access Key ID (omita se usar IAM Role no EC2) | — |
| `S3_SECRET_KEY` | Secret Access Key (omita se usar IAM Role no EC2) | — |
| `S3_ENDPOINT_URL` | Endpoint customizado para MinIO/R2 (vazio = AWS) | — |
| `S3_PREFIX` | Prefixo de chave dentro do bucket | `agronomy-api` |
| `S3_PRESIGNED_URL_TTL` | TTL das presigned URLs em segundos | `900` |

Em modo `s3`, **nenhum arquivo é gravado em disco** — uploads vão direto para o S3,
índices são gerados em temp e enviados ao S3, downloads são feitos via streaming pela API
(sem redirect, sem exposição do bucket ao frontend).

> Para testar a conectividade: `py scripts/test_s3.py`

### Deploy

| Variável | Descrição | Default |
|---|---|---|
| `PUBLIC_DOMAIN` | Hostname para Let's Encrypt no Caddy | `localhost` |
| `ACME_EMAIL` | Email para registro ACME | `admin@example.com` |
| `GUNICORN_WORKERS` | Workers do gunicorn | `4` |

### Exemplos de `LLM_MODEL`

```
gpt-4o-2024-11-20
azure/gpt-4o-pageindex
gemini/gemini-1.5-pro
anthropic/claude-sonnet-4-6
```

---

## Endpoints

```
# Autenticação
POST   /auth/login                     Login (form-data: username, password, remember_me)
POST   /auth/refresh                   Renova access token (valida user ativo)
POST   /auth/change-password           Troca senha (autenticado)
POST   /auth/forgot-password           Solicita reset (idempotente, anti-enumeração)
POST   /auth/reset-password            Aplica reset com token do email

# Usuários (admin) e perfil próprio
GET    /users/me                       Dados do usuário logado
PATCH  /users/me                       Atualiza nome
POST   /users/me/password              Troca senha
GET    /users/me/profile               Perfil agronômico (estado, cultura, etc.)
PATCH  /users/me/profile               Atualiza perfil
POST   /users/me/avatar                Upload avatar (≤5 MB)
DELETE /users/me/avatar                Remove avatar
GET    /users/{id}/avatar?token=...    Download de avatar (token JWT efêmero)
GET    /users                          Lista usuários (admin)
POST   /users                          Cria usuário (admin)
PATCH  /users/{id}                     Atualiza (admin)
DELETE /users/{id}                     Remove (admin)

# Pedidos de acesso (público)
POST   /access-requests                Solicita acesso (rate-limit 3/h)
GET    /access-requests                Lista (admin)
POST   /access-requests/{id}/decide    Aprovar/rejeitar (admin)
DELETE /access-requests/{id}           Remove (admin)

# Base de conhecimento (compartilhada)
GET    /knowledge                      Lista
POST   /knowledge                      Indexa documento (admin, async via worker)
GET    /knowledge/{id}                 Detalhes + URL de download
GET    /knowledge/{id}/file?token=...  Download
GET    /knowledge/{id}/status          Status da indexação
GET    /knowledge/stats                Métricas
DELETE /knowledge/{id}                 Remove (admin)

# Documentos do usuário (persistente, owner-only)
GET    /my-documents
POST   /my-documents                   Upload + indexação async
GET    /my-documents/{id}
GET    /my-documents/{id}/file?token=...
DELETE /my-documents/{id}

# Documentos de sessão (TTL 24h)
GET    /documents
POST   /documents                      Upload + indexação síncrona
DELETE /documents/{id}

# Conversas
GET    /conversations
GET    /conversations/{id}
PATCH  /conversations/{id}             Renomeia ou pin/unpin
DELETE /conversations/{id}

# Consulta RAG
POST   /query                          Pergunta com citações inline

# Busca global
GET    /search?q=...                   Conversas + documentos + (admins) usuários

# Configurações runtime (admin)
GET    /settings
PUT    /settings

# Infra
GET    /health                         Liveness probe (sem checar deps)
GET    /healthz/ready                  Readiness — pinga DB + Redis (200 ou 503)
```

### Exemplo de consulta

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Qual a dose de calcário para meu solo?",
    "user_data": { "pH": 5.2, "V_percent": 38, "textura": "argilosa" }
  }'
```

```json
{
  "conversation_id": "f4a6...",
  "answer": "Para solo argiloso com pH 5.2 e V% 38, a dose recomendada é 2.8 t/ha [1]. O cálculo segue o método SMP [2]...",
  "sources": [
    { "ref": 1, "doc_id": 12, "doc_name": "Manual EMBRAPA Calcário", "page": 42, "section": "Tabela 3.1" },
    { "ref": 2, "doc_id": 12, "doc_name": "Manual EMBRAPA Calcário", "page": 38, "section": "Método SMP" }
  ],
  "model_used": "azure/gpt-4o-pageindex"
}
```

---

## Segurança

A app foi auditada e endurecida em maio/2026. Detalhes em
[`docs/security-pentest-2026-05-08.md`](docs/security-pentest-2026-05-08.md).

### Defesas em runtime

- **JWT** sempre com `exp`, `iat` e `jti`. Refresh token consulta DB e bloqueia user inativo.
- **CORS** allowlist explícita (sem `*`).
- **Security headers** em toda response: HSTS, CSP, X-Frame-Options=DENY, X-Content-Type-Options=nosniff, Referrer-Policy, Permissions-Policy.
- **Rate-limiting** (slowapi + Redis):
  - `POST /auth/login` → 10/min por IP
  - `POST /auth/forgot-password` → 5/h
  - `POST /auth/reset-password` → 10/h
  - `POST /auth/refresh` → 30/min
  - `POST /access-requests` → 3/h
- **Anti-enumeração** em login, forgot-password e access-requests.
- **Uploads seguros**:
  - Storage filename é UUID (sem path traversal).
  - Cap por extensão: PDF 50 MB, DOCX/XLSX 20 MB, CSV 10 MB, JSON/MD/TXT 5 MB.
  - Avatar ≤5 MB.
  - Streaming com abort em 413 quando o cliente excede o cap.
- **Parsers hardened**:
  - `assert_zip_safe` em DOCX/XLSX (cap 200 MB descomprimido, ratio ≤100×, ≤10k entries, anti zip-slip).
  - `defusedxml` global (anti billion-laughs / XXE).
  - JSON com cap de 10 MB e profundidade ≤64.
  - CSV com `nrows=100k` e `low_memory=True`.
- **Download tokens** JWT efêmeros (15 min) com `user_id` embutido — só emissor ativo recebe o arquivo.
- **`/query`**:
  - Param `model` só aceito de admins ou se estiver em `ALLOWED_LLM_MODELS`.
  - Conversation messages truncadas em 100 (anti DoS por usuário).
- **Senha mínima 8 chars** em todos os endpoints.
- **Email injection blocked** — `html.escape` em todos os interpolados (full_name, reason, etc.).
- **`SECRET_KEY`** validada no boot — boot falha em produção (`DEBUG=false`) se valor for default.

### Saúde operacional

- `/health` (liveness) é barato e não toca DB/Redis.
- `/healthz/ready` (readiness) só retorna 200 se DB e Redis pingam.
- Slowapi com `swallow_errors=True`: se Redis cai, app continua respondendo (rate-limit é desligado em vez de 500).

### O que ainda está em backlog

- MFA para admins (TOTP)
- Logging estruturado (JSON) + ship pra ELK/Loki
- Sentry para exceptions
- Métricas Prometheus
- Backup automático Postgres (`pg_dump` para S3)
- Cleanup jobs (password_reset_tokens usados; session_documents — coberto pelo S3 Lifecycle em prod)
- Testes automatizados (pasta `tests/` ainda vazia)

---

## Migrations (Alembic)

Schema é gerenciado por Alembic. Mais detalhes em [`docs/alembic.md`](docs/alembic.md).

```powershell
# Aplica migrations pendentes:
py -m alembic upgrade head

# Cria nova migration a partir de mudanças nos models:
py -m alembic revision --autogenerate -m "descricao curta"
#   ⚠ revisar autogenerate antes de commitar — não cobre rename, server_default, etc.

# Rollback de uma:
py -m alembic downgrade -1

# Ver revision atual do DB:
py -m alembic current

# DB legado já com schema (cria flag sem rodar DDL):
py -m alembic stamp head
```

Em produção (Docker), o `entrypoint.sh` chama `alembic upgrade head` antes do gunicorn.
Para múltiplas réplicas, prefira rodar via job dedicado:

```bash
docker compose run --rm api migrate
```

E setar `SKIP_MIGRATIONS=1` nas réplicas.

---

## Deploy em produção

A imagem da API é publicada automaticamente no Docker Hub em
**[`eduardomnzs/agronomy-api`](https://hub.docker.com/r/eduardomnzs/agronomy-api)** a cada push
para `main` via GitHub Actions.

O deploy recomendado usa **Portainer + Docker Swarm + caddy-docker-proxy** para TLS automático
com domínios personalizados (`agronomy.gaek.com.br` para o frontend,
`agronomy-api.gaek.com.br` para a API).

---

### CI/CD — GitHub Actions

O workflow `.github/workflows/docker-publish.yml` faz build e push automáticos:

- **Trigger:** push para `main`
- **Tags geradas:** `latest` + `sha-<commit>` (rollback fácil)
- **Cache de layers:** registry cache (`buildcache` tag) para builds rápidos
- **Portainer webhook:** se `PORTAINER_WEBHOOK_URL` estiver definido, o Portainer repuxa a
  nova imagem automaticamente após o push

**Secrets necessários no GitHub** (`Settings → Secrets → Actions`):

| Secret | Valor |
|---|---|
| `DOCKERHUB_USERNAME` | `eduardomnzs` |
| `DOCKERHUB_TOKEN` | Access token do Docker Hub (não a senha) |
| `PORTAINER_WEBHOOK_URL` | URL do webhook do stack no Portainer (opcional) |

---

### Pré-requisitos no servidor

1. **Docker Swarm** inicializado:
   ```bash
   docker swarm init
   ```

2. **Rede de ingress** (compartilhada com o Caddy):
   ```bash
   docker network create --driver overlay --attachable ingress-network
   ```

3. **caddy-docker-proxy** rodando na rede `ingress-network` (cria os Caddy labels automaticamente):
   ```bash
   docker service create \
     --name caddy \
     --publish 80:80 \
     --publish 443:443 \
     --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
     --mount type=volume,source=caddy_data,target=/data \
     --network ingress-network \
     --constraint node.role==manager \
     lucaslorentz/caddy-docker-proxy:ci-alpine
   ```
   > Se já tiver um Caddy rodando, apenas garanta que ele usa a rede `ingress-network`.

---

### 1. Configurar S3

1. Crie o bucket S3 com **Block all public access** ativado.
2. Crie um IAM user/role com a policy mínima:
   ```json
   {
     "Effect": "Allow",
     "Action": ["s3:PutObject","s3:GetObject","s3:DeleteObject","s3:HeadObject"],
     "Resource": "arn:aws:s3:::SEU-BUCKET/agronomy-api/*"
   }
   ```
3. Em EC2, prefira **IAM Role** (sem chaves no `.env`).
4. (Opcional) S3 Lifecycle rule no prefixo `agronomy-api/sessions/`: expirar em 1 dia.
5. Teste antes do deploy:
   ```bash
   python scripts/test_s3.py
   ```

---

### 2. DNS

Aponte os registros A/AAAA para o IP do servidor:

```
agronomy.gaek.com.br       → IP do servidor   (frontend)
agronomy-api.gaek.com.br   → IP do servidor   (backend)
```

Caddy emite certs Let's Encrypt automaticamente no primeiro request HTTPS.

---

### 3. Provisionar variáveis de ambiente no Portainer

No Portainer, ao criar o stack, preencha as variáveis de ambiente (aba **Environment
variables**) ou crie um arquivo `.env` no servidor e passe via `--env-file`.

Variáveis obrigatórias:

```bash
SECRET_KEY=<64-bytes urlsafe — python -c "import secrets; print(secrets.token_urlsafe(64))">
POSTGRES_PASSWORD=<senha-forte>
REDIS_PASSWORD=<senha-forte>
OPENAI_API_KEY=<ou azure/gemini/anthropic>
RESEND_API_KEY=<chave-resend>
FROM_EMAIL=noreply@gaek.com.br
APP_BASE_URL=https://agronomy.gaek.com.br
ALLOWED_ORIGINS=https://agronomy.gaek.com.br

# S3
STORAGE_BACKEND=s3
S3_BUCKET=<nome-do-bucket>
S3_REGION=us-east-1
S3_ACCESS_KEY=<access-key>     # omita se usar IAM Role no EC2
S3_SECRET_KEY=<secret-key>     # idem

# Imagens
API_IMAGE=eduardomnzs/agronomy-api:latest
FRONTEND_IMAGE=<sua-imagem-de-frontend>
```

---

### 4. Deploy do stack via Portainer

1. No Portainer, acesse **Stacks → Add stack**.
2. Nome: `agronomy`
3. Tipo: **Repository** (cola o Git repo) **ou** **Upload** (sobe o `docker-stack.yml`).
4. Preencha as variáveis de ambiente acima.
5. Clique em **Deploy the stack**.

O serviço `agronomy-api` expõe automaticamente os labels:
```yaml
caddy: agronomy-api.gaek.com.br
caddy.reverse_proxy: "{{upstreams 8000}}"
```
O caddy-docker-proxy configura o Caddy em tempo real — sem tocar em `Caddyfile`.

---

### 5. Deploy manual (sem Portainer)

```bash
# No manager do Swarm, com as variáveis no ambiente ou arquivo .env:
docker stack deploy \
  --with-registry-auth \
  --compose-file docker-stack.yml \
  agronomy
```

---

### 6. Criar admin inicial

```bash
# Via Portainer: Stacks → agronomy → agronomy-api → Console
# Via SSH:
docker exec -it $(docker ps -qf name=agronomy_agronomy-api) \
  python scripts/seed.py --email admin@empresa.com.br --name "Administrador"
# A senha gerada é impressa uma única vez → troque no primeiro login
```

---

### 7. Portainer Webhook (redeploy automático)

Para que o GitHub Actions dispare o redeploy no Portainer:

1. No Portainer, abra o stack `agronomy` → **Webhooks** → copie a URL.
2. Adicione como secret `PORTAINER_WEBHOOK_URL` no GitHub.
3. A cada push para `main`, o CI faz build + push + chama o webhook → Portainer repuxa
   `eduardomnzs/agronomy-api:latest`.

---

### 8. Healthcheck

```
GET /healthz/ready
  → 200 {"status":"ready","checks":{"database":{"ok":true},"redis":{"ok":true}}}
  → 503 quando alguma dependência falha
```

Configure como probe de readiness no seu load-balancer ou no Docker Swarm:
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/healthz/ready"]
  interval: 30s
  timeout: 10s
  retries: 3
```

---

### 9. Comandos úteis (Swarm)

```bash
# Ver status dos serviços:
docker service ls

# Logs em tempo real:
docker service logs -f agronomy_agronomy-api

# Forçar redeploy (nova imagem):
docker service update --image eduardomnzs/agronomy-api:latest agronomy_agronomy-api

# Escalar worker:
docker service scale agronomy_agronomy-worker=2

# Rodar migration manual:
docker exec -it $(docker ps -qf name=agronomy_agronomy-api) alembic upgrade head

# Acessar Postgres:
docker exec -it $(docker ps -qf name=agronomy_agronomy-db) \
  psql -U agronomy -d agronomy
```

---

## Estrutura do projeto

```
agronomy-api/
├── alembic/                      Migrations (env.py, versions/)
├── alembic.ini
├── api/
│   ├── deps.py                   JWT dependency injection
│   ├── rate_limit.py             slowapi limiter compartilhado
│   ├── uploads.py                save_upload_async, safe_extension, UUID storage
│   └── routes/
│       ├── auth.py               /auth/*
│       ├── access_requests.py    /access-requests/*
│       ├── conversations.py      /conversations/*
│       ├── documents.py          /documents/*  (sessão, TTL 24h)
│       ├── knowledge.py          /knowledge/*  (base compartilhada)
│       ├── my_documents.py       /my-documents/*  (owner-only)
│       ├── query.py              /query
│       ├── search.py             /search
│       ├── settings.py           /settings  (admin)
│       └── users.py              /users/*
├── core/
│   ├── config.py                 Settings (validação SECRET_KEY, caps de upload, allowlists, S3)
│   ├── app_settings.py           Settings runtime (DB-backed) com Fernet
│   ├── storage.py                Backend de storage: LocalStorage / S3Storage + cache de índices
│   ├── indexer.py                Integração PageIndex (PDF + outros via .md), S3-aware
│   ├── query_engine.py           Pipeline RAG: catalog → router → agent
│   ├── agent.py                  Tool-using agent (search/get_page_content/etc.)
│   ├── router.py                 Router LLM que escolhe documentos relevantes
│   ├── llm.py                    Wrapper LiteLLM (cache, retries)
│   ├── tree_tools.py             DocContext + ferramentas do agente
│   └── citations.py              extract_inline_citations, build_context_block
├── db/
│   ├── models.py                 SQLAlchemy 2 (Mapped/DeclarativeBase)
│   └── session.py                Engine + get_db()
├── parsers/
│   ├── base.py                   Interface BaseParser + ParsedDocument
│   ├── csv_parser.py
│   ├── docx_parser.py            (com defusedxml + assert_zip_safe)
│   ├── json_parser.py            (com safe_load_json)
│   ├── md_parser.py
│   ├── pdf_parser.py
│   ├── xlsx_parser.py            (com defusedxml + assert_zip_safe)
│   ├── safety.py                 assert_zip_safe, safe_load_json, UnsafeFileError
│   └── factory.py                get_parser(file_path)
├── services/
│   ├── auth.py                   JWT (exp/iat/jti), bcrypt, MIN_PASSWORD_LEN
│   └── email.py                  Resend + html.escape, _safe_url
├── scripts/
│   ├── entrypoint.sh             Modos: api / worker / migrate
│   ├── seed.py                   Cria admin inicial (senha gerada)
│   └── test_s3.py                Testa conectividade S3 (upload, exists, download, delete)
├── worker/
│   ├── settings.py               ARQ WorkerSettings
│   └── tasks.py                  task_index_document, task_index_user_document
├── docs/
│   ├── alembic.md
│   ├── knowledge_template.md
│   └── security-pentest-2026-05-08.md
├── data/                         Arquivos + indexes (gitignored, montado como volume)
├── logs/                         (gitignored)
├── .github/
│   └── workflows/
│       └── docker-publish.yml    CI/CD: build + push Docker Hub + Portainer webhook
├── Caddyfile                     Reverse proxy + Let's Encrypt
├── Dockerfile                    Multi-stage com uv (Astral) + non-root + tini
├── docker-compose.yml            Stack de produção (api, worker, db, redis, caddy)
├── docker-compose.dev.yml        Override dev (expõe portas, desativa Caddy)
├── docker-stack.yml              Portainer/Swarm stack (caddy-docker-proxy)
├── .dockerignore
├── .gitattributes                Força LF em *.sh
├── .env.example
├── main.py                       FastAPI app + middlewares
├── requirements.txt
├── alembic.ini
├── CLAUDE.md                     Instruções para o Claude Code (dev tool)
└── README.md
```

---

## Documentação adicional

- [`docs/alembic.md`](docs/alembic.md) — fluxo de migrations
- [`docs/knowledge_template.md`](docs/knowledge_template.md) — template para metadados de documentos
- [`CLAUDE.md`](CLAUDE.md) — instruções de arquitetura para a IA

## Licença

Uso interno. Sem licença pública definida.
