# Gestão de Salas de Aula, Laboratórios e Espaços Físicos

Aplicativo web em **Streamlit** com banco **SQLite nativo (`sqlite3`)** para gestão de espaços escolares, reservas e horários.

## Perfis de usuário

1. **Administrador (`admin`)**
   - Acesso total: cadastra espaços, cria horários/reservas, gerencia manutenção, vincula alunos aos horários e exporta dados.
2. **Operador de Reservas (`reservas`)**
   - Pode criar e atualizar reservas/horários para professores.
3. **Aluno (`aluno`)**
   - Faz login e visualiza apenas o **Meu horário**.

> Sem autenticação, o usuário vê apenas a tela de **Dashboard**.

## Funcionalidades

- Cadastro de espaços (sala de aula, laboratório e outros ambientes)
- Gestão de reservas/horários com validação de conflito
- Vinculação de alunos aos horários
- Gestão de manutenção
- Dashboard com indicadores
- Relatórios e exportação CSV

## Como executar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Credenciais iniciais

- Administrador: `admin` / `admin123`
- Operador de reservas: `reserva` / `reserva123`
- Aluno: `aluno1` / `aluno123`

## Banco de dados

O arquivo SQLite é criado automaticamente na raiz do projeto:

- `gestao_espacos.db`
