"""
Cria o primeiro usuário admin no banco de dados.

Uso:
    python scripts/seed.py
    python scripts/seed.py --email admin@empresa.com --name "Admin" --password "senha123"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.models import Base, User, UserRole
from db.session import engine, SessionLocal
from services.auth import hash_password


def seed(email: str, name: str, password: str):
    Base.metadata.create_all(bind=engine)

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
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print("Admin criado com sucesso!")
        print(f"  Email : {admin.email}")
        print(f"  Nome  : {admin.full_name}")
        print(f"  Role  : {admin.role.value}")
        print(f"  ID    : {admin.id}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed do banco de dados — cria usuário admin")
    parser.add_argument("--email", default="admin@agronomy.com")
    parser.add_argument("--name", default="Administrador")
    parser.add_argument("--password", default="admin123")
    args = parser.parse_args()

    seed(email=args.email, name=args.name, password=args.password)
