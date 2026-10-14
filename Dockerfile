FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt requirements-device.txt ./
RUN pip install --no-cache-dir -r requirements-device.txt
COPY . .
RUN useradd -r -u 10001 gym && chown -R gym /srv
USER gym
EXPOSE 8000
CMD ["sh", "-c", "flask db upgrade && gunicorn -b 0.0.0.0:8000 -w 3 wsgi:app"]
