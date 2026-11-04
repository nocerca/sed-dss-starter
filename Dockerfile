FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY pyproject.toml ./
RUN python -c "import subprocess,sys,tomllib; deps=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; subprocess.run([sys.executable,'-m','pip','install','--no-cache-dir','setuptools>=70','wheel',*deps],check=True)"
COPY app ./app
COPY config ./config
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
RUN useradd -m -u 10001 appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
