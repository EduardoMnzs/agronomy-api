"""Testa a conectividade S3 usando o storage module do projeto.

Execução:
    py scripts/test_s3.py
"""
import sys
import uuid
from pathlib import Path

# Garante que o root do projeto está no path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from core.config import settings

print(f"STORAGE_BACKEND : {settings.STORAGE_BACKEND}")
print(f"S3_BUCKET       : {settings.S3_BUCKET}")
print(f"S3_REGION       : {settings.S3_REGION}")
print(f"S3_PREFIX       : {settings.S3_PREFIX}")
print(f"S3_ENDPOINT_URL : {settings.S3_ENDPOINT_URL or '(AWS padrão)'}")
print()

if settings.STORAGE_BACKEND != "s3":
    print("AVISO: STORAGE_BACKEND nao e 's3' -- configure o .env e rode novamente.")
    sys.exit(1)

from core.storage import _storage, reset_singleton
reset_singleton()

st = _storage()
test_key = f"_test/{uuid.uuid4().hex}.txt"
payload = b"agronomy-api s3 test ok"

print(f"[1/5] Upload  -> {test_key}")
st.upload(test_key, payload)
print("      OK")

print("[2/5] Exists  ->", end=" ")
assert st.exists(test_key), "FALHOU - objeto nao encontrado apos upload"
print("True  OK")

print("[3/5] Download ->", end=" ")
downloaded = st.download_bytes(test_key)
assert downloaded == payload, f"FALHOU - conteudo diverge: {downloaded!r}"
print("conteudo confere  OK")

print("[4/5] Presigned URL ->")
url = st.presigned_url(test_key, ttl=60)
print(f"      {url[:80]}...  OK")

print("[5/5] Delete  ->", end=" ")
st.delete(test_key)
assert not st.exists(test_key), "FALHOU - objeto ainda existe apos delete"
print("removido  OK")

print()
print("S3 funcionando corretamente.")
