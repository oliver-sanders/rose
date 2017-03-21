#!/bin/bash
#-------------------------------------------------------------------------------
# (C) British Crown Copyright 2012-7 Met Office.
#
# This file is part of Rose, a framework for meteorological suites.
#
# Rose is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Rose is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Rose. If not, see <http://www.gnu.org/licenses/>.
#-------------------------------------------------------------------------------
# Test the URL validator used in t/docs/00-urls.t.
#-------------------------------------------------------------------------------
. $(dirname $0)/test_header
#-------------------------------------------------------------------------------
tests 4
#-------------------------------------------------------------------------------
TEST_KEY=${TEST_KEY_BASE}
# export SPHINX_DEV_MODE=true  # For development, don't rebuild the virtualenv.
run_pass ${TEST_KEY} make -C ${ROSE_HOME}/doc doctest
file_grep ${TEST_KEY}-tests-setup "0 failures in setup code" ${TEST_KEY}.out
file_grep ${TEST_KEY}-tests-run "0 failures in tests" ${TEST_KEY}.out
file_grep ${TEST_KEY}-tests-clean "0 failures in cleanup code" ${TEST_KEY}.out
#-------------------------------------------------------------------------------
