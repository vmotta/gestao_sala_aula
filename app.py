import hashlib
import sqlite3
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path("gestao_espacos.db")

ROLES = {
    "admin": "Administrador",
    "reservas": "Operador de Reservas",
    "aluno": "Aluno",
}


# -----------------------------
# Banco de dados (SQLite nativo)
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def migrate_users_table_if_needed(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if not row or not row["sql"]:
        return

    sql = (row["sql"] or "").lower()
    if "viewer" not in sql:
        return

    conn.executescript(
        """
        ALTER TABLE users RENAME TO users_old;

        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO users (id, username, password_hash, full_name, role, status, created_at)
        SELECT id,
               username,
               password_hash,
               full_name,
               CASE role
                   WHEN 'viewer' THEN 'aluno'
                   ELSE role
               END,
               status,
               created_at
        FROM users_old;

        DROP TABLE users_old;
        """
    )


def init_db() -> None:
    with closing(get_conn()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS spaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                space_type TEXT NOT NULL CHECK(space_type IN ('Sala de Aula', 'Laboratório', 'Outro Espaço')),
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
                title TEXT NOT NULL,
                requester TEXT NOT NULL,
                purpose TEXT,
                start_dt TEXT NOT NULL,
                end_dt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pendente' CHECK(status IN ('Pendente', 'Confirmado', 'Cancelado')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(space_id) REFERENCES spaces(id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS maintenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                space_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL CHECK(priority IN ('Baixa', 'Média', 'Alta', 'Crítica')),
                scheduled_date TEXT NOT NULL,
                expected_cost REAL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'Aberta' CHECK(status IN ('Aberta', 'Em andamento', 'Concluída')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(space_id) REFERENCES spaces(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS student_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id INTEGER NOT NULL,
                student_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(booking_id, student_user_id),
                FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
                FOREIGN KEY(student_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )

        migrate_users_table_if_needed(conn)

        default_users = [
            ("admin", hash_password("admin123"), "Administrador", "admin", "active"),
            ("reserva", hash_password("reserva123"), "Operador Reservas", "reservas", "active"),
            ("aluno1", hash_password("aluno123"), "Aluno Exemplo", "aluno", "active"),
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO users (username, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            default_users,
        )


def fetch_df(query: str, params: tuple = ()) -> pd.DataFrame:
    with closing(get_conn()) as conn:
        return pd.read_sql_query(query, conn, params=params)


def execute(query: str, params: tuple = ()) -> None:
    with closing(get_conn()) as conn, conn:
        conn.execute(query, params)


def fetch_one(query: str, params: tuple = ()):
    with closing(get_conn()) as conn:
        return conn.execute(query, params).fetchone()


# -----------------------------
# Regras de negócio
# -----------------------------
def has_booking_conflict(
    space_id: int,
    start_dt: str,
    end_dt: str,
    ignore_booking_id: int | None = None,
) -> bool:
    sql = """
    SELECT COUNT(1) as total
    FROM bookings
    WHERE space_id = ?
      AND status IN ('Pendente', 'Confirmado')
      AND datetime(start_dt) < datetime(?)
      AND datetime(end_dt) > datetime(?)
    """
    params = [space_id, end_dt, start_dt]

    if ignore_booking_id is not None:
        sql += " AND id <> ?"
        params.append(ignore_booking_id)

    with closing(get_conn()) as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return bool(row["total"])


def dt_to_iso(d: date, t: time) -> str:
    return datetime.combine(d, t).strftime("%Y-%m-%d %H:%M:%S")


def load_spaces_options() -> dict[str, int]:
    df = fetch_df("SELECT id, name FROM spaces WHERE status <> 'Inativo' ORDER BY name")
    return {row["name"]: int(row["id"]) for _, row in df.iterrows()}


def load_students_options() -> dict[str, int]:
    df = fetch_df("SELECT id, full_name FROM users WHERE role = 'aluno' AND status = 'active' ORDER BY full_name")
    return {f"{row['full_name']} (#{int(row['id'])})": int(row["id"]) for _, row in df.iterrows()}


def authenticate(username: str, password: str):
    row = fetch_one(
        """
        SELECT id, username, full_name, role
        FROM users
        WHERE username = ?
          AND password_hash = ?
          AND status = 'active'
        """,
        (username.strip(), hash_password(password)),
    )
    return row


# -----------------------------
# UI - autenticação
# -----------------------------
def render_auth_sidebar() -> None:
    st.sidebar.markdown("## Acesso")

    if "auth_user" not in st.session_state:
        st.session_state.auth_user = None

    if st.session_state.auth_user is None:
        with st.sidebar.form("login_form"):
            username = st.text_input("Usuário")
            password = st.text_input("Senha", type="password")
            submitted = st.form_submit_button("Entrar")

            if submitted:
                user = authenticate(username, password)
                if user:
                    st.session_state.auth_user = {
                        "id": int(user["id"]),
                        "username": user["username"],
                        "full_name": user["full_name"],
                        "role": user["role"],
                    }
                    st.rerun()
                else:
                    st.error("Usuário ou senha inválidos.")

        st.sidebar.caption("Padrão: admin/admin123 | reserva/reserva123 | aluno1/aluno123")
        st.sidebar.info("Sem login, apenas dashboard público.")
        return

    user = st.session_state.auth_user
    st.sidebar.success(f"Logado como: {user['full_name']}")
    st.sidebar.caption(f"Perfil: {ROLES.get(user['role'], user['role'])}")
    if st.sidebar.button("Sair"):
        st.session_state.auth_user = None
        st.rerun()


# -----------------------------
# UI - páginas
# -----------------------------
def page_dashboard():
    st.subheader("Dashboard")

    col1, col2, col3, col4 = st.columns(4)
    total_spaces = fetch_df("SELECT COUNT(*) as total FROM spaces").iloc[0]["total"]
    active_bookings = fetch_df("SELECT COUNT(*) as total FROM bookings WHERE status = 'Confirmado'").iloc[0]["total"]
    pending_bookings = fetch_df("SELECT COUNT(*) as total FROM bookings WHERE status = 'Pendente'").iloc[0]["total"]
    open_maint = fetch_df("SELECT COUNT(*) as total FROM maintenance WHERE status <> 'Concluída'").iloc[0]["total"]

    col1.metric("Espaços", int(total_spaces))
    col2.metric("Reservas confirmadas", int(active_bookings))
    col3.metric("Reservas pendentes", int(pending_bookings))
    col4.metric("Manutenções abertas", int(open_maint))

    proximas = fetch_df(
        """
        SELECT s.name AS espaco, b.title AS evento, b.start_dt AS inicio, b.end_dt AS fim, b.status
        FROM bookings b
        JOIN spaces s ON s.id = b.space_id
        WHERE datetime(b.end_dt) >= datetime('now')
        ORDER BY datetime(b.start_dt)
        LIMIT 10
        """
    )
    st.markdown("### Próximas atividades")
    st.dataframe(proximas, use_container_width=True)


def page_spaces(can_manage: bool):
    st.subheader("Espaços físicos")

    if can_manage:
        with st.expander("➕ Novo espaço", expanded=False):
            with st.form("form_new_space", clear_on_submit=True):
                name = st.text_input("Nome do espaço*")
                c1, c2, c3 = st.columns(3)
                space_type = c1.selectbox("Tipo*", ["Sala de Aula", "Laboratório", "Outro Espaço"])
                building = c2.text_input("Bloco/Prédio*")
                floor = c3.text_input("Andar")
                capacity = st.number_input("Capacidade*", min_value=0, step=1, value=30)
                resources = st.text_area("Recursos")
                status = st.selectbox("Status", ["Ativo", "Inativo", "Em manutenção"])
                notes = st.text_area("Observações")
                submitted = st.form_submit_button("Salvar espaço")

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
                                (name.strip(), space_type, building.strip(), floor.strip(), int(capacity), resources.strip(), status, notes.strip()),
                            )
                            st.success("Espaço cadastrado.")
                        except sqlite3.IntegrityError as exc:
                            st.error(f"Erro ao salvar: {exc}")
    else:
        st.info("Somente administradores podem cadastrar/editar espaços.")

    spaces_df = fetch_df(
        """
        SELECT id, name, space_type AS tipo, building AS predio,
               floor AS andar, capacity AS capacidade,
               resources AS recursos, status, notes AS observacoes
        FROM spaces
        ORDER BY name
        """
    )
    st.dataframe(spaces_df, use_container_width=True)

    if not can_manage or spaces_df.empty:
        return

    idx = {row["name"]: int(row["id"]) for _, row in spaces_df.iterrows()}
    c1, c2 = st.columns([2, 1])
    chosen = c1.selectbox("Espaço", list(idx.keys()))
    new_status = c2.selectbox("Novo status", ["Ativo", "Inativo", "Em manutenção"])
    b1, b2 = st.columns(2)
    if b1.button("Atualizar status", use_container_width=True):
        execute("UPDATE spaces SET status = ? WHERE id = ?", (new_status, idx[chosen]))
        st.success("Status atualizado.")
    if b2.button("Excluir espaço", use_container_width=True):
        try:
            execute("DELETE FROM spaces WHERE id = ?", (idx[chosen],))
            st.success("Espaço excluído.")
        except sqlite3.IntegrityError:
            st.error("Não é possível excluir espaço com reservas associadas.")


def page_bookings(can_create: bool, can_assign_students: bool):
    st.subheader("Reservas / Horários")
    spaces = load_spaces_options()
    if not spaces:
        st.warning("Cadastre ao menos um espaço ativo.")
        return

    if can_create:
        with st.expander("➕ Nova reserva (horário)", expanded=False):
            with st.form("form_new_booking", clear_on_submit=True):
                title = st.text_input("Título*")
                requester = st.text_input("Professor/Solicitante*")
                space_name = st.selectbox("Espaço*", list(spaces.keys()))
                purpose = st.text_area("Finalidade")
                c1, c2 = st.columns(2)
                start_date = c1.date_input("Data início", value=date.today())
                start_time = c2.time_input("Hora início", value=time(8, 0))
                c3, c4 = st.columns(2)
                end_date = c3.date_input("Data fim", value=date.today())
                end_time = c4.time_input("Hora fim", value=time(10, 0))
                status = st.selectbox("Status", ["Pendente", "Confirmado", "Cancelado"])
                submitted = st.form_submit_button("Salvar")

                if submitted:
                    if not title.strip() or not requester.strip():
                        st.error("Título e solicitante são obrigatórios.")
                    else:
                        start_iso = dt_to_iso(start_date, start_time)
                        end_iso = dt_to_iso(end_date, end_time)
                        if end_iso <= start_iso:
                            st.error("Fim deve ser maior que início.")
                        elif has_booking_conflict(spaces[space_name], start_iso, end_iso):
                            st.error("Conflito de horário no espaço selecionado.")
                        else:
                            execute(
                                """
                                INSERT INTO bookings (space_id, title, requester, purpose, start_dt, end_dt, status)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (spaces[space_name], title.strip(), requester.strip(), purpose.strip(), start_iso, end_iso, status),
                            )
                            st.success("Reserva/horário criado.")
    else:
        st.info("Seu perfil não pode criar reservas.")

    bookings_df = fetch_df(
        """
        SELECT b.id, s.name AS espaco, b.title AS titulo, b.requester AS professor,
               b.start_dt AS inicio, b.end_dt AS fim, b.status
        FROM bookings b
        JOIN spaces s ON s.id = b.space_id
        ORDER BY datetime(b.start_dt) DESC
        """
    )
    st.dataframe(bookings_df, use_container_width=True)

    if bookings_df.empty:
        return

    if can_create:
        st.markdown("### Atualizações rápidas")
        bmap = {f"#{int(r['id'])} - {r['titulo']} ({r['espaco']})": int(r["id"]) for _, r in bookings_df.iterrows()}
        c1, c2 = st.columns([3, 1])
        chosen = c1.selectbox("Reserva", list(bmap.keys()))
        new_status = c2.selectbox("Novo status", ["Pendente", "Confirmado", "Cancelado"])
        u1, u2 = st.columns(2)
        if u1.button("Atualizar", use_container_width=True):
            execute("UPDATE bookings SET status = ? WHERE id = ?", (new_status, bmap[chosen]))
            st.success("Reserva atualizada.")
        if u2.button("Excluir", use_container_width=True):
            execute("DELETE FROM bookings WHERE id = ?", (bmap[chosen],))
            st.success("Reserva excluída.")

    if can_assign_students:
        st.markdown("### Vincular alunos ao horário")
        students = load_students_options()
        if not students:
            st.info("Não há alunos cadastrados.")
            return

        bmap = {f"#{int(r['id'])} - {r['titulo']} ({r['espaco']})": int(r["id"]) for _, r in bookings_df.iterrows()}
        c1, c2 = st.columns(2)
        selected_booking = c1.selectbox("Horário", list(bmap.keys()), key="assign_booking")
        selected_student = c2.selectbox("Aluno", list(students.keys()))
        if st.button("Vincular aluno"):
            try:
                execute(
                    "INSERT INTO student_bookings (booking_id, student_user_id) VALUES (?, ?)",
                    (bmap[selected_booking], students[selected_student]),
                )
                st.success("Aluno vinculado ao horário.")
            except sqlite3.IntegrityError:
                st.warning("Esse aluno já está vinculado a este horário.")


def page_student_schedule(student_user_id: int):
    st.subheader("Meu horário")
    df = fetch_df(
        """
        SELECT b.id,
               b.title AS disciplina_atividade,
               b.requester AS professor,
               s.name AS espaco,
               b.start_dt AS inicio,
               b.end_dt AS fim,
               b.status
        FROM student_bookings sb
        JOIN bookings b ON b.id = sb.booking_id
        JOIN spaces s ON s.id = b.space_id
        WHERE sb.student_user_id = ?
        ORDER BY datetime(b.start_dt)
        """,
        (student_user_id,),
    )

    if df.empty:
        st.info("Você ainda não possui horários vinculados.")
    else:
        st.dataframe(df, use_container_width=True)


def page_maintenance(can_manage: bool):
    st.subheader("Manutenção")
    spaces = load_spaces_options()

    if can_manage and spaces:
        with st.expander("➕ Novo chamado", expanded=False):
            with st.form("form_maintenance", clear_on_submit=True):
                space_name = st.selectbox("Espaço", list(spaces.keys()))
                description = st.text_area("Descrição*")
                c1, c2, c3 = st.columns(3)
                priority = c1.selectbox("Prioridade", ["Baixa", "Média", "Alta", "Crítica"])
                scheduled_date = c2.date_input("Data prevista", value=date.today())
                cost = c3.number_input("Custo estimado", min_value=0.0, step=100.0)
                status = st.selectbox("Status", ["Aberta", "Em andamento", "Concluída"])
                submitted = st.form_submit_button("Salvar")
                if submitted:
                    if not description.strip():
                        st.error("Descrição obrigatória.")
                    else:
                        execute(
                            """
                            INSERT INTO maintenance (space_id, description, priority, scheduled_date, expected_cost, status)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (spaces[space_name], description.strip(), priority, scheduled_date.isoformat(), float(cost), status),
                        )
                        st.success("Chamado criado.")
    elif not can_manage:
        st.info("Somente administradores podem gerir manutenção.")

    df = fetch_df(
        """
        SELECT m.id, s.name AS espaco, m.description AS descricao, m.priority AS prioridade,
               m.scheduled_date AS data_prevista, m.expected_cost AS custo, m.status
        FROM maintenance m
        JOIN spaces s ON s.id = m.space_id
        ORDER BY date(m.scheduled_date) DESC
        """
    )
    st.dataframe(df, use_container_width=True)


def page_reports(show_users: bool):
    st.subheader("Relatórios")

    report = fetch_df(
        """
        SELECT s.name AS espaco, s.space_type AS tipo,
               COUNT(b.id) AS reservas_confirmadas
        FROM spaces s
        LEFT JOIN bookings b ON b.space_id = s.id AND b.status = 'Confirmado'
        GROUP BY s.id, s.name, s.space_type
        ORDER BY reservas_confirmadas DESC
        """
    )
    st.markdown("### Ocupação por espaço")
    st.dataframe(report, use_container_width=True)

    tables = ["spaces", "bookings", "maintenance", "student_bookings"]
    if show_users:
        tables.append("users")

    table = st.selectbox("Exportar tabela", tables)
    data = fetch_df(f"SELECT * FROM {table}")
    st.download_button(
        label=f"Baixar {table}.csv",
        data=data.to_csv(index=False).encode("utf-8"),
        file_name=f"{table}.csv",
        mime="text/csv",
    )


def main():
    st.set_page_config(page_title="Gestão de Espaços Escolares", page_icon="🏫", layout="wide")
    init_db()

    st.title("🏫 Gestão de salas, laboratórios e espaços físicos")
    st.caption("Com perfis: Administrador, Operador de Reservas e Aluno.")

    render_auth_sidebar()
    user = st.session_state.get("auth_user")

    if user is None:
        page_dashboard()
        return

    role = user["role"]
    is_admin = role == "admin"
    is_reservation = role == "reservas"
    is_student = role == "aluno"

    if is_student:
        menu = st.sidebar.radio("Navegação", ["Dashboard", "Meu horário"])
        if menu == "Dashboard":
            page_dashboard()
        else:
            page_student_schedule(int(user["id"]))
        return

    if is_reservation:
        menu = st.sidebar.radio("Navegação", ["Dashboard", "Reservas"])
        if menu == "Dashboard":
            page_dashboard()
        else:
            page_bookings(can_create=True, can_assign_students=False)
        return

    menu = st.sidebar.radio(
        "Navegação",
        ["Dashboard", "Espaços", "Reservas", "Manutenção", "Relatórios"],
    )
    if menu == "Dashboard":
        page_dashboard()
    elif menu == "Espaços":
        page_spaces(can_manage=True)
    elif menu == "Reservas":
        page_bookings(can_create=True, can_assign_students=True)
    elif menu == "Manutenção":
        page_maintenance(can_manage=True)
    else:
        page_reports(show_users=True)


if __name__ == "__main__":
    main()
