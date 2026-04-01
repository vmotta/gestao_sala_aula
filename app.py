import hashlib
import sqlite3
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path("gestao_espacos.db")


# -----------------------------
# Banco de dados (SQLite)
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_db() -> None:
    with closing(get_conn()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'professor')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS spaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                space_type TEXT NOT NULL CHECK(space_type IN ('Sala de Aula', 'Laboratório')),
                building TEXT NOT NULL,
                floor TEXT,
                capacity INTEGER NOT NULL CHECK(capacity >= 0),
                resources TEXT,
                status TEXT NOT NULL DEFAULT 'Ativo' CHECK(status IN ('Ativo', 'Inativo', 'Em manutenção')),
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                space_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                purpose TEXT,
                start_dt TEXT NOT NULL,
                end_dt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pendente' CHECK(status IN ('Pendente', 'Confirmado', 'Cancelado')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(space_id) REFERENCES spaces(id) ON DELETE RESTRICT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE RESTRICT
            );
            """
        )

        # Garante usuário admin padrão
        admin_exists = conn.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'").fetchone()["total"]
        if not admin_exists:
            conn.execute(
                """
                INSERT INTO users (name, email, password_hash, role)
                VALUES (?, ?, ?, 'admin')
                """,
                ("Administrador", "admin@escola.local", hash_password("admin123")),
            )


def fetch_df(query: str, params: tuple = ()) -> pd.DataFrame:
    with closing(get_conn()) as conn:
        return pd.read_sql_query(query, conn, params=params)


def execute(query: str, params: tuple = ()) -> None:
    with closing(get_conn()) as conn, conn:
        conn.execute(query, params)


# -----------------------------
# Regras de negócio
# -----------------------------
def dt_to_iso(d: date, t: time) -> str:
    return datetime.combine(d, t).strftime("%Y-%m-%d %H:%M:%S")


def load_space_options() -> dict[str, int]:
    df = fetch_df("SELECT id, name FROM spaces WHERE status = 'Ativo' ORDER BY name")
    return {row["name"]: int(row["id"]) for _, row in df.iterrows()}


def has_booking_conflict(space_id: int, start_dt: str, end_dt: str, ignore_booking_id: int | None = None) -> bool:
    sql = """
    SELECT COUNT(1) AS total
    FROM bookings
    WHERE space_id = ?
      AND status IN ('Pendente', 'Confirmado')
      AND datetime(start_dt) < datetime(?)
      AND datetime(end_dt) > datetime(?)
    """
    params: list[int | str] = [space_id, end_dt, start_dt]

    if ignore_booking_id is not None:
        sql += " AND id <> ?"
        params.append(ignore_booking_id)

    with closing(get_conn()) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return bool(row["total"])


def authenticate(email: str, password: str):
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? AND active = 1",
            (email.strip().lower(),),
        ).fetchone()
        if not row:
            return None
        if row["password_hash"] == hash_password(password):
            return dict(row)
    return None


# -----------------------------
# UI - Autenticação
# -----------------------------
def show_login():
    st.title("🏫 Sistema de Salas e Laboratórios")
    st.caption("Login obrigatório para administrador e professores")

    with st.form("form_login"):
        email = st.text_input("E-mail")
        password = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar")

        if submitted:
            user = authenticate(email, password)
            if user:
                st.session_state["user"] = user
                st.success(f"Bem-vindo(a), {user['name']}!")
                st.rerun()
            else:
                st.error("Credenciais inválidas ou usuário inativo.")

    with st.expander("Credenciais iniciais"):
        st.info("Admin padrão: admin@escola.local / admin123")


# -----------------------------
# UI - Funcionalidades comuns
# -----------------------------
def page_reserve_room(current_user: dict):
    st.subheader("Reservar salas e laboratórios")
    spaces = load_space_options()

    if not spaces:
        st.warning("Não há salas/laboratórios ativos para reserva.")
        return

    with st.form("form_new_booking", clear_on_submit=True):
        title = st.text_input("Título da reserva*", placeholder="Aula de Programação")
        purpose = st.text_area("Finalidade")
        space_name = st.selectbox("Sala/Laboratório*", list(spaces.keys()))

        c1, c2 = st.columns(2)
        start_date = c1.date_input("Data de início", value=date.today())
        start_time = c2.time_input("Hora de início", value=time(8, 0))

        c3, c4 = st.columns(2)
        end_date = c3.date_input("Data de término", value=date.today())
        end_time = c4.time_input("Hora de término", value=time(10, 0))

        status_default = "Confirmado" if current_user["role"] == "admin" else "Pendente"
        submitted = st.form_submit_button("Salvar reserva")

        if submitted:
            if not title.strip():
                st.error("Informe o título da reserva.")
                return

            start_iso = dt_to_iso(start_date, start_time)
            end_iso = dt_to_iso(end_date, end_time)

            if end_iso <= start_iso:
                st.error("A data/hora final deve ser maior que a inicial.")
            elif has_booking_conflict(spaces[space_name], start_iso, end_iso):
                st.error("Conflito de horário com outra reserva ativa.")
            else:
                execute(
                    """
                    INSERT INTO bookings (space_id, user_id, title, purpose, start_dt, end_dt, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        spaces[space_name],
                        int(current_user["id"]),
                        title.strip(),
                        purpose.strip(),
                        start_iso,
                        end_iso,
                        status_default,
                    ),
                )
                st.success("Reserva criada com sucesso.")


def page_overview_board():
    st.subheader("Quadro de visualização geral")

    df = fetch_df(
        """
        SELECT b.id,
               s.name AS sala,
               s.space_type AS tipo,
               u.name AS professor,
               b.title AS titulo,
               b.start_dt AS inicio,
               b.end_dt AS fim,
               b.status
        FROM bookings b
        JOIN spaces s ON s.id = b.space_id
        JOIN users u ON u.id = b.user_id
        WHERE date(b.start_dt) >= date('now', '-7 day')
        ORDER BY datetime(b.start_dt) ASC
        """
    )

    st.dataframe(df, use_container_width=True)


def page_room_board():
    st.subheader("Quadro de visualização por sala")

    rooms = fetch_df("SELECT id, name FROM spaces ORDER BY name")
    if rooms.empty:
        st.info("Nenhuma sala/laboratório cadastrado.")
        return

    room_map = {row["name"]: int(row["id"]) for _, row in rooms.iterrows()}
    selected = st.selectbox("Escolha a sala/laboratório", list(room_map.keys()))

    df = fetch_df(
        """
        SELECT b.id,
               u.name AS professor,
               b.title AS titulo,
               b.purpose AS finalidade,
               b.start_dt AS inicio,
               b.end_dt AS fim,
               b.status
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        WHERE b.space_id = ?
        ORDER BY datetime(b.start_dt) DESC
        """,
        (room_map[selected],),
    )

    st.dataframe(df, use_container_width=True)


def page_my_bookings(current_user: dict):
    st.subheader("Minhas reservas")

    query = """
    SELECT b.id,
           s.name AS sala,
           b.title AS titulo,
           b.start_dt AS inicio,
           b.end_dt AS fim,
           b.status
    FROM bookings b
    JOIN spaces s ON s.id = b.space_id
    """
    params: tuple = ()

    if current_user["role"] != "admin":
        query += " WHERE b.user_id = ?"
        params = (int(current_user["id"]),)

    query += " ORDER BY datetime(b.start_dt) DESC"
    df = fetch_df(query, params)
    st.dataframe(df, use_container_width=True)

    if df.empty:
        return

    booking_map = {f"#{int(r['id'])} - {r['titulo']} ({r['sala']})": int(r["id"]) for _, r in df.iterrows()}
    selected = st.selectbox("Selecione uma reserva", list(booking_map.keys()))

    cols = st.columns(2)
    if cols[0].button("Cancelar reserva", use_container_width=True):
        execute("UPDATE bookings SET status = 'Cancelado' WHERE id = ?", (booking_map[selected],))
        st.success("Reserva cancelada.")

    if current_user["role"] == "admin" and cols[1].button("Confirmar reserva", use_container_width=True):
        execute("UPDATE bookings SET status = 'Confirmado' WHERE id = ?", (booking_map[selected],))
        st.success("Reserva confirmada.")


# -----------------------------
# UI - Admin
# -----------------------------
def page_manage_spaces():
    st.subheader("Cadastro e gestão de salas/laboratórios")

    with st.expander("➕ Cadastrar sala/laboratório"):
        with st.form("form_new_space", clear_on_submit=True):
            name = st.text_input("Nome*")
            c1, c2, c3 = st.columns(3)
            space_type = c1.selectbox("Tipo*", ["Sala de Aula", "Laboratório"])
            building = c2.text_input("Prédio/Bloco*")
            floor = c3.text_input("Andar")
            capacity = st.number_input("Capacidade*", min_value=0, step=1, value=30)
            resources = st.text_area("Recursos")
            status = st.selectbox("Status", ["Ativo", "Inativo", "Em manutenção"])
            notes = st.text_area("Observações")
            submitted = st.form_submit_button("Salvar")

            if submitted:
                if not name.strip() or not building.strip():
                    st.error("Preencha os campos obrigatórios.")
                else:
                    try:
                        execute(
                            """
                            INSERT INTO spaces (name, space_type, building, floor, capacity, resources, status, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                name.strip(),
                                space_type,
                                building.strip(),
                                floor.strip(),
                                int(capacity),
                                resources.strip(),
                                status,
                                notes.strip(),
                            ),
                        )
                        st.success("Sala/laboratório cadastrado com sucesso.")
                    except sqlite3.IntegrityError:
                        st.error("Já existe um espaço com esse nome.")

    df = fetch_df(
        """
        SELECT id, name, space_type AS tipo, building AS predio, floor AS andar,
               capacity AS capacidade, resources AS recursos, status, notes AS observacoes
        FROM spaces
        ORDER BY name
        """
    )
    st.dataframe(df, use_container_width=True)


def page_manage_users():
    st.subheader("Gerenciar usuários")

    with st.expander("➕ Novo usuário"):
        with st.form("form_new_user", clear_on_submit=True):
            name = st.text_input("Nome*")
            email = st.text_input("E-mail*")
            password = st.text_input("Senha inicial*", type="password")
            role = st.selectbox("Perfil", ["professor", "admin"])
            submitted = st.form_submit_button("Cadastrar usuário")

            if submitted:
                if not name.strip() or not email.strip() or not password.strip():
                    st.error("Preencha todos os campos obrigatórios.")
                else:
                    try:
                        execute(
                            """
                            INSERT INTO users (name, email, password_hash, role, active)
                            VALUES (?, ?, ?, ?, 1)
                            """,
                            (name.strip(), email.strip().lower(), hash_password(password.strip()), role),
                        )
                        st.success("Usuário criado com sucesso.")
                    except sqlite3.IntegrityError:
                        st.error("E-mail já cadastrado.")

    users_df = fetch_df(
        """
        SELECT id, name, email, role, active, created_at
        FROM users
        ORDER BY role, name
        """
    )
    st.dataframe(users_df, use_container_width=True)

    if users_df.empty:
        return

    umap = {f"#{int(r['id'])} - {r['name']} ({r['role']})": int(r["id"]) for _, r in users_df.iterrows()}
    selected = st.selectbox("Selecionar usuário", list(umap.keys()))
    c1, c2 = st.columns(2)

    if c1.button("Ativar usuário", use_container_width=True):
        execute("UPDATE users SET active = 1 WHERE id = ?", (umap[selected],))
        st.success("Usuário ativado.")

    if c2.button("Inativar usuário", use_container_width=True):
        execute("UPDATE users SET active = 0 WHERE id = ?", (umap[selected],))
        st.success("Usuário inativado.")


def page_admin_reports():
    st.subheader("Painel do administrador")

    c1, c2, c3, c4 = st.columns(4)
    total_spaces = fetch_df("SELECT COUNT(*) AS total FROM spaces").iloc[0]["total"]
    total_prof = fetch_df("SELECT COUNT(*) AS total FROM users WHERE role = 'professor' AND active = 1").iloc[0]["total"]
    total_bookings = fetch_df("SELECT COUNT(*) AS total FROM bookings").iloc[0]["total"]
    pending = fetch_df("SELECT COUNT(*) AS total FROM bookings WHERE status = 'Pendente'").iloc[0]["total"]

    c1.metric("Salas/Labs", int(total_spaces))
    c2.metric("Professores ativos", int(total_prof))
    c3.metric("Reservas", int(total_bookings))
    c4.metric("Pendentes", int(pending))


# -----------------------------
# Main
# -----------------------------
def main():
    st.set_page_config(page_title="Gestão de Salas e Laboratórios", page_icon="🏫", layout="wide")
    init_db()

    user = st.session_state.get("user")

    if not user:
        show_login()
        return

    st.title("🏫 Gestão de Salas e Laboratórios")
    st.caption(f"Usuário logado: {user['name']} ({user['role']})")

    if st.sidebar.button("Sair"):
        st.session_state.pop("user", None)
        st.rerun()

    if user["role"] == "admin":
        menu = st.sidebar.radio(
            "Navegação",
            [
                "Painel",
                "Gerenciar Salas/Labs",
                "Gerenciar Usuários",
                "Reservar",
                "Gerenciar Reservas",
                "Quadro Geral",
                "Quadro por Sala",
            ],
        )

        if menu == "Painel":
            page_admin_reports()
        elif menu == "Gerenciar Salas/Labs":
            page_manage_spaces()
        elif menu == "Gerenciar Usuários":
            page_manage_users()
        elif menu == "Reservar":
            page_reserve_room(user)
        elif menu == "Gerenciar Reservas":
            page_my_bookings(user)
        elif menu == "Quadro Geral":
            page_overview_board()
        else:
            page_room_board()

    else:
        menu = st.sidebar.radio(
            "Navegação",
            ["Reservar", "Minhas Reservas", "Quadro Geral", "Quadro por Sala"],
        )

        if menu == "Reservar":
            page_reserve_room(user)
        elif menu == "Minhas Reservas":
            page_my_bookings(user)
        elif menu == "Quadro Geral":
            page_overview_board()
        else:
            page_room_board()


if __name__ == "__main__":
    main()
