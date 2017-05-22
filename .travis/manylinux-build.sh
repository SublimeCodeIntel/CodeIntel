#!/bin/bash
set -e -x

cd /io/

# Compile wheels
for PYBIN in /opt/python/cp${PYTHON_VERSION}*/bin; do
    "${PYBIN}/python" setup.py bdist_wheel
done

# Bundle external shared libraries into the wheels
for whl in dist/*.whl; do
    auditwheel repair "$whl" -w /io/dist/
done
