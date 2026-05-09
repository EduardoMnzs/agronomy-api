"""Configura CORS no bucket S3 para permitir downloads diretos pelo browser.

Execucao:
    py scripts/configure_s3_cors.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from core.config import settings
from core.storage import _storage, reset_singleton

if settings.STORAGE_BACKEND != "s3":
    print("AVISO: STORAGE_BACKEND nao e 's3'. Nada a fazer.")
    sys.exit(0)

reset_singleton()
st = _storage()

# Origens permitidas: inclui dev (localhost) e producao
allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]

# Garante que origens comuns de dev estao presentes
for dev_origin in ["http://localhost:5173", "http://localhost:3000"]:
    if dev_origin not in allowed_origins:
        allowed_origins.append(dev_origin)

cors_config = {
    "CORSRules": [
        {
            "AllowedHeaders": ["*"],
            "AllowedMethods": ["GET", "HEAD"],
            "AllowedOrigins": allowed_origins,
            "ExposeHeaders": [
                "Content-Disposition",
                "Content-Type",
                "Content-Length",
                "ETag",
            ],
            "MaxAgeSeconds": 3600,
        }
    ]
}

print(f"Bucket  : {settings.S3_BUCKET}")
print(f"Origens : {allowed_origins}")
print()

st._s3.put_bucket_cors(
    Bucket=settings.S3_BUCKET,
    CORSConfiguration=cors_config,
)

# Verifica o que foi salvo
result = st._s3.get_bucket_cors(Bucket=settings.S3_BUCKET)
print("CORS salvo no bucket:")
print(json.dumps(result["CORSRules"], indent=2))
print()
print("CORS configurado com sucesso.")
