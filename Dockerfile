FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 3000


# Um único worker/thread: o poller de automação roda em background dentro do
# processo e o storage usa SQLite, que não suporta múltiplos processos
# escrevendo concorrentemente. Não aumente --workers sem migrar o storage
# para um banco que suporte concorrência entre processos (ex.: Postgres).
CMD ["gunicorn", "-b", "0.0.0.0:3000", "app:create_app()", "--timeout", "60", "--workers", "1", "--threads", "4"]
