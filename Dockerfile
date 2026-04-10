FROM python:3.10-slim

# Install system dependencies for matplotlib and OpenSees
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran \
    liblapack-dev libblas-dev \
    libgomp1 \
    tcl8.6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import openseespy.opensees as ops; print('openseespy', ops.version())"

COPY . .

# Create data directories
RUN mkdir -p data/uploads data/runs

EXPOSE 10000

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "2", "--timeout", "600", "--threads", "4"]
