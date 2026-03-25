FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    curl \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-java-common \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . /app/

# Ensure necessary directories exist
RUN mkdir -p /app/uploads/prontuario /app/instance

# Expose the application port
EXPOSE 5010

# Command to start the application with Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5010", "--workers", "3", "--timeout", "120", "app:app"]
