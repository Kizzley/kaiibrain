FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=3000
ENV RAILWAY_DATABASE_URL=

EXPOSE 3000

CMD ["python", "shtpass_railway_app.py"]
