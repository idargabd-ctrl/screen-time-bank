# Приложение учебного банка времени.
#
# Тот же образ, что используется для тестов, — значит на сервере не появляется
# второй набор версий Python и зависимостей.
FROM python:3.13-slim

WORKDIR /app

# Зависимости отдельным слоем: правка кода не пересобирает установку пакетов.
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY app ./app
COPY content ./content
COPY tools ./tools

# База и .env приходят монтированием, в образ не попадают.
EXPOSE 8000

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
