import sqlite3
from contextlib import closing
from datetime import datetime, date, time
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path("gestao_espacos.db")


# -----------------------------
# Banco de dados (SQLite nativo)
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    with closing(get_conn()) as conn, conn:
        conn.executescript(
            """
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
            """
        )


def fetch_df(query: str, params: tuple = ()) -> pd.DataFrame:
    with closing(get_conn()) as conn:
        return pd.read_sql_query(query, conn, params=params)


def execute(query: str, params: tuple = ()) -> None:
    with closing(get_conn()) as conn, conn:
        conn.execute(query, params)


def execute_many(query: str, params_seq: list[tuple]) -> None:
    with closing(get_conn()) as conn, conn:
        conn.executemany(query, params_seq)


# -----------------------------
# Regras de negócio
# -----------------------------
def has_booking_conflict(space_id: int, start_dt: str, end_dt: str, ignore_booking_id: int | None = None) -> bool:
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


# -----------------------------
# UI
# -----------------------------
def page_dashboard():
    st.subheader("Visão geral")

    col1, col2, col3, col4 = st.columns(4)
    total_spaces = fetch_df("SELECT COUNT(*) as total FROM spaces").iloc[0]["total"]
    active_bookings = fetch_df("SELECT COUNT(*) as total FROM bookings WHERE status = 'Confirmado'").iloc[0]["total"]
    pending_bookings = fetch_df("SELECT COUNT(*) as total FROM bookings WHERE status = 'Pendente'").iloc[0]["total"]
    open_maint = fetch_df("SELECT COUNT(*) as total FROM maintenance WHERE status <> 'Concluída'").iloc[0]["total"]

    col1.metric("Espaços cadastrados", int(total_spaces))
    col2.metric("Reservas confirmadas", int(active_bookings))
    col3.metric("Reservas pendentes", int(pending_bookings))
    col4.metric("Manutenções abertas", int(open_maint))

    st.markdown("### Ocupação por tipo")
    ocup = fetch_df(
        """
        SELECT s.space_type,
               COUNT(b.id) AS reservas
        FROM spaces s
        LEFT JOIN bookings b ON b.space_id = s.id
        AND b.status = 'Confirmado'
        AND date(b.start_dt) = date('now')
        GROUP BY s.space_type
        ORDER BY reservas DESC
        """
    )
    st.dataframe(ocup, use_container_width=True)

    st.markdown("### Próximas reservas")
    proximas = fetch_df(
        """
        SELECT b.id, s.name AS espaco, b.title AS evento, b.requester AS solicitante,
               b.start_dt AS inicio, b.end_dt AS fim, b.status
        FROM bookings b
        JOIN spaces s ON s.id = b.space_id
        WHERE datetime(b.end_dt) >= datetime('now')
        ORDER BY datetime(b.start_dt)
        LIMIT 20
        """
    )
    st.dataframe(proximas, use_container_width=True)


def page_spaces():
    st.subheader("Cadastro de salas, laboratórios e espaços físicos")

    with st.expander("➕ Novo espaço", expanded=False):
        with st.form("form_new_space", clear_on_submit=True):
            name = st.text_input("Nome do espaço*", placeholder="Ex.: Laboratório de Química 02")
            col1, col2, col3 = st.columns(3)
            space_type = col1.selectbox("Tipo*", ["Sala de Aula", "Laboratório", "Outro Espaço"])
            building = col2.text_input("Bloco/Prédio*", placeholder="Bloco A")
            floor = col3.text_input("Andar")
            capacity = st.number_input("Capacidade*", min_value=0, step=1, value=30)
            resources = st.text_area("Recursos", placeholder="Projetor, ar-condicionado, computadores...")
            status = st.selectbox("Status", ["Ativo", "Inativo", "Em manutenção"])
            notes = st.text_area("Observações")
            submitted = st.form_submit_button("Salvar espaço")

            if submitted:
                if not name.strip() or not building.strip():
                    st.error("Preencha os campos obrigatórios marcados com *.")
                else:
                    try:
                        execute(
                            """
                            INSERT INTO spaces (name, space_type, building, floor, capacity, resources, status, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (name.strip(), space_type, building.strip(), floor.strip(), int(capacity), resources.strip(), status, notes.strip()),
                        )
                        st.success("Espaço cadastrado com sucesso!")
                    except sqlite3.IntegrityError as exc:
                        st.error(f"Não foi possível salvar: {exc}")

    spaces_df = fetch_df(
        """
        SELECT id, name, space_type AS tipo, building AS predio,
               floor AS andar, capacity AS capacidade,
               resources AS recursos, status, notes AS observacoes, created_at
        FROM spaces
        ORDER BY name
        """
    )
    st.markdown("### Espaços cadastrados")
    st.dataframe(spaces_df, use_container_width=True)

    st.markdown("### ✏️ Atualizar status de espaço")
    if spaces_df.empty:
        st.info("Cadastre um espaço para habilitar ações de atualização e remoção.")
        return

    name_to_id = {row["name"]: int(row["id"]) for _, row in spaces_df.iterrows()}
    col1, col2 = st.columns([2, 1])
    selected_space = col1.selectbox("Escolha o espaço", list(name_to_id.keys()))
    new_status = col2.selectbox("Novo status", ["Ativo", "Inativo", "Em manutenção"])
    col3, col4 = st.columns(2)

    if col3.button("Atualizar status", use_container_width=True):
        execute("UPDATE spaces SET status = ? WHERE id = ?", (new_status, name_to_id[selected_space]))
        st.success("Status atualizado.")

    if col4.button("Excluir espaço", type="secondary", use_container_width=True):
        try:
            execute("DELETE FROM spaces WHERE id = ?", (name_to_id[selected_space],))
            st.success("Espaço excluído.")
        except sqlite3.IntegrityError:
            st.error("Não é possível excluir espaços com reservas associadas.")


def page_bookings():
    st.subheader("Gestão de reservas")
    spaces = load_spaces_options()

    if not spaces:
        st.warning("Cadastre ao menos um espaço ativo para criar reservas.")
        return

    with st.expander("➕ Nova reserva", expanded=False):
        with st.form("form_new_booking", clear_on_submit=True):
            title = st.text_input("Título da reserva*", placeholder="Aula de Física - 2º ano")
            requester = st.text_input("Solicitante*", placeholder="Coordenação / Professor")
            space_name = st.selectbox("Espaço*", list(spaces.keys()))
            purpose = st.text_area("Finalidade")

            c1, c2 = st.columns(2)
            start_date = c1.date_input("Data de início", value=date.today())
            start_time = c2.time_input("Hora de início", value=time(8, 0))

            c3, c4 = st.columns(2)
            end_date = c3.date_input("Data de término", value=date.today())
            end_time = c4.time_input("Hora de término", value=time(10, 0))

            status = st.selectbox("Status", ["Pendente", "Confirmado", "Cancelado"])
            submitted = st.form_submit_button("Salvar reserva")

            if submitted:
                if not title.strip() or not requester.strip():
                    st.error("Preencha os campos obrigatórios.")
                else:
                    start_iso = dt_to_iso(start_date, start_time)
                    end_iso = dt_to_iso(end_date, end_time)

                    if end_iso <= start_iso:
                        st.error("A data/hora de término deve ser maior que a de início.")
                    elif has_booking_conflict(spaces[space_name], start_iso, end_iso):
                        st.error("Conflito detectado: já existe reserva ativa neste intervalo.")
                    else:
                        execute(
                            """
                            INSERT INTO bookings (space_id, title, requester, purpose, start_dt, end_dt, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (spaces[space_name], title.strip(), requester.strip(), purpose.strip(), start_iso, end_iso, status),
                        )
                        st.success("Reserva criada com sucesso!")

    bookings_df = fetch_df(
        """
        SELECT b.id,
               s.name AS espaco,
               b.title AS titulo,
               b.requester AS solicitante,
               b.purpose AS finalidade,
               b.start_dt AS inicio,
               b.end_dt AS fim,
               b.status
        FROM bookings b
        JOIN spaces s ON s.id = b.space_id
        ORDER BY datetime(b.start_dt) DESC
        """
    )
    st.markdown("### Reservas")
    st.dataframe(bookings_df, use_container_width=True)

    if not bookings_df.empty:
        st.markdown("### Ações rápidas")
        bmap = {f"#{int(r['id'])} - {r['titulo']} ({r['espaco']})": int(r["id"]) for _, r in bookings_df.iterrows()}
        c1, c2 = st.columns([3, 1])
        chosen = c1.selectbox("Reserva", list(bmap.keys()))
        new_status = c2.selectbox("Novo status", ["Pendente", "Confirmado", "Cancelado"], key="booking_status")

        cc1, cc2 = st.columns(2)
        if cc1.button("Atualizar reserva", use_container_width=True):
            execute("UPDATE bookings SET status = ? WHERE id = ?", (new_status, bmap[chosen]))
            st.success("Reserva atualizada.")

        if cc2.button("Excluir reserva", use_container_width=True):
            execute("DELETE FROM bookings WHERE id = ?", (bmap[chosen],))
            st.success("Reserva excluída.")


def page_maintenance():
    st.subheader("Gestão de manutenção")
    spaces = load_spaces_options()

    if not spaces:
        st.warning("Cadastre espaços antes de abrir chamados de manutenção.")
        return

    with st.expander("➕ Novo chamado", expanded=False):
        with st.form("form_new_maintenance", clear_on_submit=True):
            space_name = st.selectbox("Espaço", list(spaces.keys()))
            description = st.text_area("Descrição do problema*")
            c1, c2, c3 = st.columns(3)
            priority = c1.selectbox("Prioridade", ["Baixa", "Média", "Alta", "Crítica"])
            scheduled_date = c2.date_input("Data prevista", value=date.today())
            cost = c3.number_input("Custo estimado (R$)", min_value=0.0, step=100.0)
            status = st.selectbox("Status", ["Aberta", "Em andamento", "Concluída"])
            submitted = st.form_submit_button("Abrir chamado")

            if submitted:
                if not description.strip():
                    st.error("Informe a descrição do problema.")
                else:
                    execute(
                        """
                        INSERT INTO maintenance (space_id, description, priority, scheduled_date, expected_cost, status)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (spaces[space_name], description.strip(), priority, scheduled_date.isoformat(), float(cost), status),
                    )
                    st.success("Chamado registrado.")

    maint_df = fetch_df(
        """
        SELECT m.id, s.name AS espaco, m.description AS descricao, m.priority AS prioridade,
               m.scheduled_date AS data_prevista, m.expected_cost AS custo_previsto, m.status
        FROM maintenance m
        JOIN spaces s ON s.id = m.space_id
        ORDER BY date(m.scheduled_date) DESC
        """
    )
    st.markdown("### Chamados")
    st.dataframe(maint_df, use_container_width=True)

    if not maint_df.empty:
        cmap = {f"#{int(r['id'])} - {r['espaco']}": int(r["id"]) for _, r in maint_df.iterrows()}
        c1, c2 = st.columns([3, 1])
        chosen = c1.selectbox("Chamado", list(cmap.keys()))
        new_status = c2.selectbox("Novo status", ["Aberta", "Em andamento", "Concluída"], key="maint_status")

        if st.button("Atualizar chamado"):
            execute("UPDATE maintenance SET status = ? WHERE id = ?", (new_status, cmap[chosen]))
            st.success("Chamado atualizado.")


def page_reports():
    st.subheader("Relatórios e exportação")

    st.markdown("### Taxa de ocupação por espaço (reservas confirmadas)")
    report = fetch_df(
        """
        SELECT s.name AS espaco,
               s.space_type AS tipo,
               COUNT(b.id) AS reservas_confirmadas
        FROM spaces s
        LEFT JOIN bookings b ON b.space_id = s.id AND b.status = 'Confirmado'
        GROUP BY s.id, s.name, s.space_type
        ORDER BY reservas_confirmadas DESC, s.name
        """
    )
    st.dataframe(report, use_container_width=True)

    st.markdown("### Custos de manutenção por status")
    costs = fetch_df(
        """
        SELECT status, ROUND(SUM(expected_cost), 2) AS custo_total
        FROM maintenance
        GROUP BY status
        ORDER BY custo_total DESC
        """
    )
    st.dataframe(costs, use_container_width=True)

    st.markdown("### Exportar dados")
    table = st.selectbox("Tabela", ["spaces", "bookings", "maintenance"])
    exported = fetch_df(f"SELECT * FROM {table}")
    csv_data = exported.to_csv(index=False).encode("utf-8")
    st.download_button(
        label=f"Baixar {table}.csv",
        data=csv_data,
        file_name=f"{table}.csv",
        mime="text/csv",
    )


def main():
    st.set_page_config(page_title="Gestão de Espaços Escolares", page_icon="🏫", layout="wide")
    init_db()

    st.title("🏫 Gestão completa de salas, laboratórios e espaços físicos")
    st.caption("Aplicativo Streamlit com SQLite nativo para cadastro, reservas, manutenção e relatórios.")

    menu = st.sidebar.radio(
        "Navegação",
        ["Dashboard", "Espaços", "Reservas", "Manutenção", "Relatórios"],
    )

    if menu == "Dashboard":
        page_dashboard()
    elif menu == "Espaços":
        page_spaces()
    elif menu == "Reservas":
        page_bookings()
    elif menu == "Manutenção":
        page_maintenance()
    else:
        page_reports()


if __name__ == "__main__":
    main()
