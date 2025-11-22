A lightweight transcode service
===============================

To build the docker image:
```
docker build -t transcode-service .
```

To run the service:
```
docker run -it --rm transcode-service
```

For developers:
```
pre-commit install # Installs a pre-commit hook for code formatting
pre-commit run --all-files # Runs code formatter manually
```
