# Gestão de Salas de Aula, Laboratórios e Espaços Físicos

Aplicativo web em **Streamlit** com banco **SQLite nativo (`sqlite3`)** para gestão completa de espaços escolares.

## Funcionalidades

- Cadastro de espaços (sala de aula, laboratório e outros ambientes)
- Controle de status do espaço (ativo, inativo, em manutenção)
- Gestão de reservas com validação de conflito de horários
- Gestão de manutenção com prioridade, status e custo estimado
- Dashboard com indicadores operacionais
- Relatórios e exportação CSV

## Como executar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Banco de dados

O arquivo SQLite é criado automaticamente na raiz do projeto:

- `gestao_espacos.db`

Não é necessário instalar servidor de banco.
