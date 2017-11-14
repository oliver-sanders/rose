Optional Configurations
=======================

Optional configurations allow you to specify additional configuration in
other files, which you can include automatically or at runtime via commands
like ``rose app-run``/``rose task-run`` (application configurations) or
``rose suite-run`` (for suite configurations).


Example
-------

.. image:: https://upload.wikimedia.org/wikipedia/commons/3/31/Ice_Cream_dessert_02.jpg
   :align: right
   :width: 250px
   :alt: Ice Cream

Our example suite will configure our selection of icecream.

Create a new suite (or just a new directory somewhere - e.g. in your
homespace) containing a blank ``rose-suite.conf`` and a ``suite.rc`` file that
looks like this:

.. code-block:: cylc

   [cylc]
       UTC mode = True # Ignore DST
   [scheduling]
       [[dependencies]]
           graph = order_icecream => eat_icecream
   [runtime]
       [[order_icecream]]
           script = rose task-run
       [[eat_icecream]]
           script = echo Yummy

We'll also need an app for ``order_icecream`` - create an
``app/order_icecream/`` subdirectory within your suite.

Add a ``rose-app.conf`` file within this directory - use this content:

.. code-block:: rose

   [command]
   default=echo "I'd like to order a $FLAVOUR icecream $CONE_TYPE with $TOPPING"

   [env]
   CONE_TYPE=cone
   FLAVOUR=vanilla
   TOPPING=no topping

Your suite is now runnable with ``rose suite-run``. Try running it and
looking at the output - it should be pretty predictable!

We can override settings within apps by passing particular options or to the
``rose task-run`` in ``script``.

The best way to find out how to use ``rose task-run`` is to look at the
help by running ``rose help task-run`` - notice that the help indicates
that options for ``rose app-run`` are supported. This is because
``rose task-run`` is effectively a wrapper for ``rose app-run``.

Have a look at the help for ``rose app-run``, by running
``rose help app-run``. You can see that we can use ``--define`` to
override things in the app at runtime.

In the ``suite.rc`` file, modify the ``script`` for the ``order_icecream``
task so that it reads:

.. code-block:: cylc

   script = rose task-run --define='[env]FLAVOUR=chocolate' --define='[env]TOPPING=fudge'

This will override the ``FLAVOUR`` and ``TOPPING`` options in the ``env``
section of our app. If you run the suite again, you should see that the
output of the task has changed.

If we had any more options to override, it might get a little unwieldy - it
would be nicer to create an optional configuration containing these
settings that we could apply on-demand, especially if they were used regularly.

Create a subdirectory ``opt/`` within the app itself
(``app/order_icecream/opt/``). This is where Rose will look for optional
configurations. Individual optional configuration files (there can be
more than one in the same directory) are named ``rose-app-NAME.conf``
where ``NAME`` is some kind of useful id.


New Optional Configurations
---------------------------

Create an optional configuration file called ``rose-app-chocofudge.conf``
in your new ``opt/`` subdirectory, with the following content:

.. code-block:: rose

   [env]
   FLAVOUR=chocolate
   TOPPING=fudge

We can now reference this through an option to ``rose task-run`` - alter
the ``script`` of the ``order_icecream`` task (in the ``suite.rc`` file)
to read:

.. code-block:: cylc

   script = rose task-run --opt-conf-key=chocofudge

This will now override the ``order_icecream`` app's ``rose-app.conf``
file with the contents of the ``rose-app-chocofudge.conf`` file at runtime.

We can add as many ``--opt-conf-key`` options to ``rose task-run`` as we
like - not just the one. This would apply each optional configuration in
first-to-last order.

Try running the suite - it should apply the optional configuration correctly.


$ROSE_APP_OPT_CONF_KEYS
-----------------------

We could also specify the optional configurations to use by passing in an
environment variable ``$ROSE_APP_OPT_CONF_KEYS``, a space delimited list
of optional configuration names to use.

Try using it by replacing the order_icecream task configuration with:

.. code-block:: cylc

   [[order_icecream]]
       script = rose task-run
       [[[environment]]]
           ROSE_APP_OPT_CONF_KEYS = chocofudge

If you re-run the suite, this should do the same job as the ``--opt-conf-key``
method we tried before.


Default Optional Configurations
-------------------------------

Optional configurations can be switched on by default by specifying their
names via a top-level opts setting in the configuration file.

Put the following line at the top of the order_icecream ``rose-app.conf`` file:

.. code-block:: rose

   opts=chocofudge

We now don't need to specify it at all for ``rose task-run`` - get rid of
the environment variable in the ``suite.rc`` file, so that the
``order_icecream`` task runtime configuration looks like this:

.. code-block:: cylc

   [[order_icecream]]
       script = rose task-run


Default Optional Configurations - Test
--------------------------------------

If you run the suite, it should have the same standard output for
``order_icecream`` as before.

Typically, because the opts setting is 'always-on', it's best to use the
optional configurations to add settings rather than override them.


Other Optional Configurations
-----------------------------

All Rose configurations can have optional configurations, not just
application configurations. Suites can have optional configurations
that override ``rose-suite.conf`` settings, controlled through
``rose suite-run``. This takes the same ``--opt-conf-key`` option as
``rose app-run`` and/or the environment variable ``$ROSE_SUITE_OPT_CONF_KEYS``.

Metadata configurations can also have optional configurations, typically
included via the opts top-level setting.
