FROM python:3.9

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/
# RUN pip install --no-cache-dir -r requirements.txt

# Install dependencies
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*  # Clean up to reduce image size

RUN pip install --no-cache-dir -r requirements.txt
RUN pip uninstall pymongo bson -y
RUN pip install pymongo==3.12.3
RUN playwright install
RUN playwright install-deps


COPY . /app/

#CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:8000", "backend.wsgi:application"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
#CMD ["gunicorn", "backend.wsgi:application", "--bind", "0.0.0.0:8000"]
