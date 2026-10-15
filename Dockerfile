FROM python:3.12-slim

WORKDIR /app
RUN mkdir -p /var/lib/signalpost && chown 10001:10001 /var/lib/signalpost
COPY signalpost /app/signalpost
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SIGNALPOST_HOST=0.0.0.0 \
    SIGNALPOST_DB=/var/lib/signalpost/signalpost.sqlite3
USER 10001:10001
EXPOSE 8000
VOLUME ["/var/lib/signalpost"]
CMD ["python", "-m", "signalpost", "serve"]
