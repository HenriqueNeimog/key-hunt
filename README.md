# Key Hunt

Key Hunt é um jogo web de treino auditivo: importe uma playlist pública do YouTube,
ouça cada faixa e tente identificar seu tom antes de revelar a análise. A ordem é
embaralhada uma única vez por sessão e persistida no SQLite. Uma janela limitada de
faixas é preparada antecipadamente, por um worker Kafka no ambiente Docker ou pelo
processador inline no desenvolvimento local.

## Arquitetura

- FastAPI, Jinja2, HTMX e JavaScript leve servem páginas, APIs e o player acessível.
- SQLAlchemy 2.x, SQLite em WAL e Alembic armazenam playlists, cache, rodadas, sessões,
  ordem imutável, claims, resultados e o transactional outbox.
- O publicador confirma cada item do outbox somente após o ack do Kafka. Eventos
  `publishing` abandonados voltam a ser elegíveis após o lease.
- O worker consome contratos Pydantic v1 com entrega at-least-once, adquire um claim
  atômico por faixa, usa yt-dlp/FFmpeg e Essentia fora do event loop e confirma o offset
  após persistir o resultado. Redelivery usa `processed_events`; falhas transitórias
  recebem backoff e falhas permanentes/esgotadas vão para a DLQ.
- O áudio e a análise válidos são cacheados globalmente em `tracks`, inclusive entre
  sessões. Arquivos são produzidos em `media/.work` e movidos atomicamente ao nome
  final antes de serem servidos.

SQLite é apropriado para esta instalação local com uma instância web e poucos workers.
Várias réplicas de escrita ou alta concorrência exigirão PostgreSQL.

## Execução local com uv

Requer Python 3.12, `uv`, FFmpeg e, para análise real, Essentia.

```powershell
cd D:\key-hunt
uv sync --all-groups --extra analysis
uv run alembic upgrade head
$env:KEY_HUNT_PROCESSING_MODE="inline"
uv run uvicorn app.main:app --reload
```

O modo `inline` não requer Kafka e usa exatamente o mesmo contrato/outbox/processador.
Para executar processos separados contra um Kafka disponível:

```powershell
$env:KEY_HUNT_PROCESSING_MODE="kafka"
$env:KEY_HUNT_KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
uv run python -m app.worker
```

## Docker (Kafka KRaft, web e worker)

```powershell
docker compose up --build -d
docker compose ps
docker compose --profile test run --rm integration-tests
docker compose logs -f web worker
docker compose down
```

O Compose usa Kafka oficial 4.3.1 em KRaft, sem ZooKeeper. Web e worker compartilham o
volume de SQLite/mídia. `PROCESSING_MODE=kafka` é obrigatório nesse ambiente.

## Qualidade e migrations

```powershell
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict
uv run pytest
uv run pytest -m integration
```

Os testes padrão usam fakes e não acessam YouTube nem Kafka. O marcador `integration`
é reservado ao broker real e à análise nativa opcional.

## Operação e segurança

As rotas que alteram estado exigem CSRF e as rodadas/sessões pertencem ao cookie
assinado do jogador. Status de sessão expõe somente a faixa atual; status de prefetch
contém contagens genéricas. Tom, escala e confiança só saem no endpoint explícito de
revelação. O diretório de mídia não é público e a rota de áudio valida o caminho.

Health checks: `/health/live` indica processo vivo e `/health/ready` valida banco e
informa Kafka como `ok`, `degraded` ou `not_configured`. Uma indisponibilidade do broker
não impede novas sessões: os trabalhos permanecem no outbox.

Para reprocessar com segurança um evento já publicado na DLQ, use seu UUID registrado
nos logs/outbox. O comando valida o contrato e os agregados, cria um novo `event_id` e
passa novamente pelo outbox (repetir o mesmo comando não duplica o replay):

```powershell
uv run python -m app.admin requeue-dlq EVENT_ID
```

Use apenas conteúdo que você tem direito de acessar. O projeto não contorna DRM,
login, playlists privadas ou restrições do YouTube. Disponibilidade e regras do YouTube,
precisão do Essentia e espaço local continuam sendo limitações externas.
