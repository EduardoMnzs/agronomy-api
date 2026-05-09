"""
Cria o primeiro usuário admin no banco.

Uso:
    py scripts/seed.py --email admin@empresa.com --name "Admin"
    py scripts/seed.py --email admin@empresa.com --name "Admin" --password "<senha forte>"

Sem --password, gera uma senha aleatória forte e imprime uma única vez.
"""
import argparse
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.models import Base, User, UserRole, UserStatus
from db.session import engine, SessionLocal
from services.auth import MIN_PASSWORD_LEN, hash_password


def _generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%&*-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def seed(email: str, name: str, password: str | None) -> None:
    Base.metadata.create_all(bind=engine)

    generated = False
    if not password:
        password = _generate_password()
        generated = True
    if len(password) < MIN_PASSWORD_LEN:
        sys.exit(f"Senha precisa ter ao menos {MIN_PASSWORD_LEN} caracteres.")

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"Usuário '{email}' já existe (role: {existing.role.value}). Nada foi alterado.")
            return

        admin = User(
            email=email,
            full_name=name,
            password_hash=hash_password(password),
            role=UserRole.admin,
            status=UserStatus.pending,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print("Admin criado com sucesso!")
        print(f"  Email : {admin.email}")
        print(f"  Nome  : {admin.full_name}")
        print(f"  Role  : {admin.role.value}")
        print(f"  ID    : {admin.id}")
        if generated:
            print()
            print(f"  Senha (gerada — guarde agora, não será mostrada de novo): {password}")
        print()
        print("  Status inicial: 'pending' — o admin precisa redefinir a senha no primeiro login.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed do banco de dados — cria usuário admin")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--password", default=None, help="Omita para gerar senha aleatória forte")
    args = parser.parse_args()

    seed(email=args.email, name=args.name, password=args.password)
