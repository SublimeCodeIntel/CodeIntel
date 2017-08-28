#!/bin/bash
set -e -x

cd /io/

# Compile wheels
for PYBIN in /opt/python/cp${PYTHON_VERSION}*/bin; do
    rm -rf "build"
    "${PYBIN}/python" setup.py bdist_wheel
done

# Bundle external shared libraries into the wheels
mv dist dist.orig
mkdir /io/dist
for whl in dist.orig/*.whl; do
    auditwheel repair "$whl" -w /io/dist/
done
