#!/usr/bin/make -f

# If sphinx-build is not installed or if too early a version is installed make
# will attempt to install latest sphinx-build via a virtualenv.
#
# export SPHINX_DEV_MODE='true' to prevent the virtualenv from being deleted
# after the build completes.

min-sphinx-version = 1.5.3  # TODO
pypath = ../lib/python

env-path = ../venv
sphinx-install := $(shell scripts/sphinx-install $(env-path) $(min-sphinx-version))
sphinx-uninstall := $(shell scripts/sphinx-uninstall)

venv =
ifneq	"$(sphinx-install)"	""
venv = . $(env-path)/bin/activate;
endif

all: doctest html

html: sphinx-build
	$(venv) PYTHONPATH=../$(pypath) make -C sphinx html

doctest: sphinx-build
	$(venv) PYTHONPATH=../$(pypath) make -C sphinx doctest

sphinx-build: $(sphinx-install)
	echo ${PWD}
	ls ../lib/python
	$(venv) PYTHONPATH=$(pypath) sphinx-build -aEW -b dummy sphinx sphinx/_build

sphinx-uninstall:
	rm -rf $(env-path)

sphinx-install: $(sphinx-uninstall)
	virtualenv --python=python2.7 "$(env-path)"
	$(venv) pip install sphinx

sphinx-activate:
