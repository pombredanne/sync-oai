# ResourceSync OAI-PMH Adapter

The ResourceSync OAI-PMH Adapter creates sitemaps and changesets to allow synchronization of resources with resync.

## Quick start

Make sure Python 2.7.1 is running on your system:

    python --version

Install the [Tornado](http://www.tornadoweb.org/) and [SleekXMPP](https://github.com/fritzy/SleekXMPP), [PyYAML](http://pyyaml.org/), and [APScheduler](http://packages.python.org/APScheduler/) libraries:

    sudo easy_install tornado
    sudo easy_install sleekxmpp    
    sudo easy_install PyYAML
    sudo easy_install apscheduler

If you miss dateutil, download it at: http://labix.org/python-dateutil, untar it:
tar xvzf python....tar.gz,
build it:
python setup.py build
and install it:
sudo python setup.py install

    
Get the ResourceSync ResourceSync OAI-PMH Adapter from [Github](http://github.com/pedak/sync-oai.git):

    git clone git://github.com/pedak/sync-oai.git
    
Run the ResourceSync OAI-PMH Adapter (with the default configuration in /config/default.yaml):
    
    chmod u+x irc-adapter
    ./irc-adapter 

or run the ResourceSync Wikipedia-IRC Adapter with static sitemap, changeset generation and given hostname

	./ircadapter.py -c config/static.yaml -n "en.wikipedia.example.org"

Run the resync client against the simulated source

    chmod u+x resync-client
    ./resync-client -s http://www.example.org/ /tmp/resync/example.org --sitemap http://localhost/sitemap.xml --noauth

Terminate the ResourceSync Wikipedia-IRC Adapter:

    CTRL-C

## How to define parameterized use cases

Parameterized Use Cases can be defined by creating a [YAML](http://www.yaml.org/) configuration file (e.g., simulation1.yaml) and defining a set of parameters:

	source:
	    name: ResourceSync OAI-Adapter
	    endpoint: http://example.org/cgi/oai2
	    max_runs: 100
	    sleep_time: 30
	    fromdate: 2011-09-13T10:00:00
	    event_types: [create, update, delete]
        
Additional **inventory**, **publisher**, and **change memory** implementations
can be attached for simulation purposes. For instance, the following configuration attaches a change memory implemented in the DynamicChangeSet class.

    inventory_builder:
        class: DynamicInventoryBuilder
        uri_path: sitemap.xml

    changememory:
        class: DynamicChangeSet
        uri_path: changeset
        max_changes: 1000
            
See the examples in the **/config** directory for further details.