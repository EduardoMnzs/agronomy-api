# Migrations — Alembic

A partir de 2026-05-09 o schema do Postgres é gerenciado por Alembic. O bloco
`Base.metadata.create_all()` + `_bootstrap_schema()` que rodava no boot do
`main.py` foi removido — toda mudança de schema agora vive em
`alembic/versions/`.

## Comandos do dia a dia

```powershell
# Aplicar todas as migrations (idempotente):
py -m alembic upgrade head

# Criar migration nova a partir de mudanças nos models (db/models.py):
py -m alembic revision --autogenerate -m "descricao curta"

# Inspecionar a migration gerada antes de commitar — autogenerate erra em
# alguns casos (renames, server_default, índices parciais).

# Ver histórico:
py -m alembic history

# Rollback de uma revision:
py -m alembic downgrade -1

# Ver em qual revision o DB está:
py -m alembic current
```

## Primeira vez em um banco vazio

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db redis
py -m alembic upgrade head
py scripts/seed.py --email admin@empresa.com --name "Admin"
py -m uvicorn main:app --host 127.0.0.1 --port 8000
```

## Migrar um banco que já tinha o schema (legado)

Se o banco já tinha as tabelas criadas pela versão antiga (`create_all`), não
queremos reaplicar o DDL. Marcamos o DB como já estando na revision atual:

```powershell
py -m alembic stamp head
```

Depois disso, qualquer migration nova é aplicada com `upgrade head` normal.

## Em produção / Docker

O `entrypoint.sh` da imagem (ver Dockerfile) roda `alembic upgrade head` antes
de iniciar o gunicorn. Em deploys com múltiplas réplicas, garanta que apenas
uma roda as migrations (ex.: job de pré-deploy, ou `flock` em arquivo
compartilhado). Alembic em si trava com `pg_advisory_lock`, mas não espera —
réplicas concorrentes podem falhar uma e outra prosseguir.

## Boas práticas

- Sempre revise o conteúdo de `alembic/versions/<rev>.py` após autogenerate.
- Para mudanças de dado (não só schema), escreva `op.execute("UPDATE ...")`
  manualmente — autogenerate não cobre isso.
- Para colunas NOT NULL adicionadas em tabela com dados, faça em 2 passos:
  1. Adicionar coluna NULLable + backfill
  2. Tornar NOT NULL
- Nunca edite uma migration depois de já ter sido aplicada num ambiente
  compartilhado — gere uma nova.
