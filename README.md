# Gestão de Salas e Laboratórios (Streamlit + SQLite)

Sistema web para escola/universidade com dois perfis:

- **Administrador**: cadastra salas/laboratórios, gerencia usuários, aprova/cancela reservas e acompanha quadros de visualização.
- **Professor**: faz reservas e visualiza quadro geral e quadro por sala/laboratório.

## Funcionalidades implementadas

- Autenticação de usuários (admin/professor)
- Cadastro e gestão de salas e laboratórios
- Cadastro e ativação/inativação de usuários
- Reserva de salas/laboratórios com detecção de conflito de horário
- Quadro geral de reservas
- Quadro de reservas por sala/laboratório
- Primeira tela pública com abas de quadro geral, quadro por sala e login
- Painel com métricas para administrador

## Tecnologias

- Python
- Streamlit
- SQLite (`sqlite3` nativo)
- Pandas

## Como executar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Credenciais iniciais

Ao iniciar o sistema pela primeira vez, é criado um administrador padrão:

- **E-mail**: `admin@escola.local`
- **Senha**: `admin123`

> Recomendado alterar a senha criando novo usuário administrador e inativando o padrão.

## Banco de dados

O arquivo SQLite é criado automaticamente na raiz do projeto:

- `gestao_espacos.db`
