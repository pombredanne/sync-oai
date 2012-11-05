#!/usr/bin/env python
# encoding: utf-8
"""
source.py: A source holds a set of resources and changes over time.

Resources are internally stored by their basename (e.g., 1) for memory
efficiency reasons.

Created by Bernhard Haslhofer on 2012-04-24.
Adopted by Peter Kalchgruber on 2012-09-01.
Copyright 2012, ResourceSync.org. All rights reserved.
"""

import re
import os
import random
import pprint
import logging
import time
import shutil

import tornado.ioloop
import tornado.web

from apscheduler.scheduler import Scheduler

from resync.observer import Observable
from resync.resource_change import ResourceChange
from resync.resource import Resource
from resync.digest import compute_md5_for_string
from resync.inventory import Inventory
from resync.sitemap import Sitemap, Mapper

import re
import socket
import urllib2
import gzip
import StringIO

from irc import IRCClient

#### Source-specific capability implementations ####

class DynamicInventoryBuilder(object):
    """Generates an inventory snapshot from a source"""
    
    def __init__(self, source, config):
        self.source = source
        self.config = config
        self.logger = logging.getLogger('inventory_builder')
        
    def bootstrap(self):
        """Bootstrapping procedures implemented in subclasses"""
        pass
    
    @property
    def path(self):
        """The inventory path (from the config file)"""
        return self.config['uri_path']

    @property
    def uri(self):
        """The inventory URI (e.g., http://localhost:8080/sitemap.xml)"""
        return self.source.base_uri + "/" + self.path
    
    def generate(self):
        """Generates an inventory (snapshot from the source)"""
        then = time.time()
        capabilities = {}
        if self.source.has_changememory:
            next_changeset = self.source.changememory.next_changeset_uri()
            capabilities[next_changeset] = {"rel": "next http://www.openarchives.org/rs/changeset"}
        inventory = Inventory(resources=self.source.resources,
                              capabilities=capabilities)
        now = time.time()
        self.logger.info("Generated inventory: %f" % (now-then))
        return inventory
        
class StaticInventoryBuilder(DynamicInventoryBuilder):
    """Periodically writes an inventory to the file system"""
    
    def __init__(self, source, config):
        super(StaticInventoryBuilder, self).__init__(source, config)
                                
    def bootstrap(self):
        """Bootstraps the static inventory writer background job"""
        self.rm_sitemap_files(Source.STATIC_FILE_PATH)
        self.write_static_inventory()
        logging.basicConfig()
        interval = self.config['interval']
        sched = Scheduler()
        sched.start()
        sched.add_interval_job(self.write_static_inventory,
                                seconds=interval)
    
    def generate(self):
        """Generates an inventory (snapshot from the source)
        TODO: remove as soon as resource container _len_ is fixed"""
        capabilities = {}
        if self.source.has_changememory:
            next_changeset = self.source.changememory.next_changeset_uri()
            capabilities[next_changeset] = {"type": "changeset"}
        # inventory = Inventory(resources=self.source.resources,
        #                       capabilities=capabilities)
        inventory = Inventory(resources=None, capabilities=capabilities)
        for resource in self.source.resources:
            if resource is not None: inventory.add(resource)
        return inventory
    
    def write_static_inventory(self):
        """Writes the inventory to the filesystem"""
        # Generate sitemap in temp directory
        then = time.time()
        self.ensure_temp_dir(Source.TEMP_FILE_PATH)
        inventory = self.generate()
        basename = Source.TEMP_FILE_PATH + "/sitemap.xml"
        s=Sitemap()
        s.max_sitemap_entries=self.config['max_sitemap_entries']
        s.mapper=Mapper([self.source.base_uri, Source.TEMP_FILE_PATH])
        s.write(inventory, basename)
        # Delete old sitemap files; move the new ones; delete the temp dir
        self.rm_sitemap_files(Source.STATIC_FILE_PATH)
        self.mv_sitemap_files(Source.TEMP_FILE_PATH, Source.STATIC_FILE_PATH)
        shutil.rmtree(Source.TEMP_FILE_PATH)
        now = time.time()
        # Log Sitemap create start event
        sitemap_size = self.compute_sitemap_size(Source.STATIC_FILE_PATH)
        log_data = {'time': (now-then), 
                    'no_resources': self.source.resource_count}
        self.logger.info("Wrote static sitemap inventory. %s" % log_data)
        sm_write_end = ResourceChange(
                resource = ResourceChange(self.uri, 
                                size=sitemap_size,
                                timestamp=then),
                                changetype = "UPDATED")
        self.source.notify_observers(sm_write_end)
        
    def ensure_temp_dir(self, temp_dir):
        """Create temp directory if it doesn't exist; removes existing one"""
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)
        else:
            os.makedirs(temp_dir)
    
    def ls_sitemap_files(self, directory):
        """Returns the list of sitemaps in a directory"""
        p = re.compile('sitemap\d*\.xml')
        filelist = [ f for f in os.listdir(directory) if p.match(f) ]
        return filelist
    
    def rm_sitemap_files(self, directory):
        """Deletes sitemap files (from previous runs)"""
        filelist = self.ls_sitemap_files(directory)
        if len(filelist) > 0:
            self.logger.debug("*** Cleaning up %d sitemap files ***" % 
                                                                len(filelist))
            for f in filelist:
                filepath = directory + "/" + f
                os.remove(filepath)
    
    def mv_sitemap_files(self, src_directory, dst_directory):
        """Moves sitemaps from src to dst directory"""
        filelist = self.ls_sitemap_files(src_directory)
        if len(filelist) > 0:
            self.logger.debug("*** Moving %d sitemap files ***" % 
                                                                len(filelist))
            for f in filelist:
                filepath = src_directory + "/" + f
                shutil.move(filepath, dst_directory)
    
    def compute_sitemap_size(self, directory):
        """Computes the size of all sitemap files in a given directory"""
        return sum([os.stat(directory + "/" + f).st_size 
                        for f in self.ls_sitemap_files(directory)])
    
#### IRC-Adapter Source ####

class Source(Observable):
    """A source contains a list of resources and changes over time"""
    
    RESOURCE_PATH = "/resources"
    STATIC_FILE_PATH = os.path.join(os.path.dirname(__file__), "static")
    TEMP_FILE_PATH = os.path.join(os.path.dirname(__file__), "temp")
    
    def __init__(self, config, hostname, port):
        """Initalize the source"""
        super(Source, self).__init__()
        self.logger = logging.getLogger('source')
        self.config = config
        self.logger.info("Source config: %s " % self.config)
        self.hostname = hostname
        self.port = port
        self.max_res_id = 1
        self._repository = {} # {basename, {timestamp, size}}
        self.inventory_builder = None # The inventory builder implementation
        self.changememory = None # The change memory implementation
        self.no_events = 0
        self.client = None
        self.host = None
        self.nick = "simplewikibot"
        self.dumpstamp = 0
    
    ##### Source capabilities #####
    
    def add_inventory_builder(self, inventory_builder):
        """Adds an inventory builder implementation"""
        self.inventory_builder = inventory_builder
        
    @property
    def has_inventory_builder(self):
        """Returns True in the Source has an inventory builder"""
        return bool(self.inventory_builder is not None)        
    
    def add_changememory(self, changememory):
        """Adds a changememory implementation"""
        self.changememory = changememory
        
    @property
    def has_changememory(self):
        """Returns True if a source maintains a change memory"""
        return bool(self.changememory is not None)
    
    ##### Bootstrap Source ######

    def bootstrap(self):
        """Bootstrap the source with a set of resources"""
        self.logger.info("Bootstrapping source")
        if self.has_changememory: self.changememory.bootstrap()
        if self.has_inventory_builder: self.inventory_builder.bootstrap()
        self._log_stats()
    
    ##### Source data accessors #####
    
    @property
    def base_uri(self):
        """Returns the base URI of the source (e.g., http://localhost:8888)"""
        return "http://" + self.hostname + ":" + str(self.port)

    @property
    def resource_count(self):
        """The number of resources in the source's repository"""
        return len(self._repository)
    
    @property
    def resources(self):
        """Iterates over resources and yields resource objects"""
        for basename in self._repository.keys():
            resource = self.resource(basename)
            if resource is None:
                self.logger.error("Cannot create resource %s " % basename + \
                      "because source object has been deleted.")
            else:
                yield resource
    
    @property
    def random_resource(self):
        """Returns a single random resource"""
        rand_res = self.random_resources()
        if len(rand_res) == 1:
            return rand_res[0]
        else:
            return None
    
    def resource(self, basename):
        """Creates and returns a resource object from internal resource
        repository. Repositoy values are copied into the object."""
        #if not self._repository.has_key(basename): return None
        uri = basename
#        timestamp = self._repository[basename]['timestamp']
        timestamp=time.time()
        return Resource(uri = uri, timestamp = timestamp)
    
    def random_resources(self, number = 1):
        "Return a random set of resources, at most all resources"
        if number > len(self._repository):
            number = len(self._repository)
        rand_basenames = random.sample(self._repository.keys(), number)
        return [self.resource(basename) for basename in rand_basenames]
    
    
    # Private Methods
    
    def _create_resource(self, basename = None, timestamp=time.time(), notify_observers = True):
        """Create a new resource, add it to the source, notify observers."""
        self._repository[basename] = {'timestamp': timestamp}
        timestamp = time.time()
        change = ResourceChange(resource = self.resource(basename),
                                changetype = "CREATED")
        if notify_observers:
            self.notify_observers(change)
            self.logger.debug("Event: %s" % repr(change))
        
    def _update_resource(self, basename):
        """Update a resource, notify observers."""
        timestamp = time.time()
        
        self._repository[basename] = {'timestamp': timestamp}
            
        change = ResourceChange(
                    resource = self.resource(basename),
                    changetype = "UPDATED")
        self.notify_observers(change)
        self.logger.debug("Event: %s" % repr(change))

    def _delete_resource(self, basename, notify_observers = True):
        """Delete a given resource, notify observers."""
        res = self.resource(basename)
        if basename in self._repository:
            del self._repository[basename]
        res.timestamp = time.time()
        
        if notify_observers:
            change = ResourceChange(resource = res, changetype = "DELETED")
            self.notify_observers(change)
            self.logger.debug("Event: %s" % repr(change))
    
    def bootstrap_irc(self,endpoint,channel): #todo update granularity
        """bootstraps IRC-PMH Source"""
        self.loadDump()
        self.channel=channel
        self.logger.debug("Connecting to Wikimedia-IRC-Endpoint %s" % endpoint)
        self.client=IRCClient(endpoint,channel,self.config['nick'],self.config['ident'],self.config['realname'])
        self.irc=self.client.connect()
        self.process()
        
    def loadDump(self):
        urlh=urllib2.urlopen(self.config['dump_file'])
        curdumpstamp=urlh.info().getheaders("Last-Modified")[0]
        if curdumpstamp != self.dumpstamp:
            self.dumpstamp=curdumpstamp
        url_f = StringIO.StringIO(urlh.read())
        unzipped_file = gzip.GzipFile(fileobj=url_f)
        self.logger.debug("Dump downloaded")
        for i,line in enumerate(unzipped_file):
            resource=self.config['uri_host']+line[:len(line)-1]
            self._create_resource(unicode(resource,"utf-8"),notify_observers=False)
            self.logger.debug("%s %screated" % (i,resource))
        self.logger.info("%s resources created at initial dump import" % i)

            
    def process(self):
        while 1:
            line = self.irc.readline().rstrip() 
            if 'rc-pmtpa' in line:
                #regex = re.compile("\x03(?:\d{1,2}(?:,\d{1,2})?)?", re.UNICODE)
                #string=regex.sub("",line)
                string=line
                self.logger.debug(string)
                match=re.search("#en.wikipedia :\x0314\[\[\x0307(.+?)\x0314\]\]\x034 (.*?)\x0310.*\x0302(.*?)\x03.+\x0303(.+?)\x03.+\x03 (.*) \x0310(.*)\x03?.*", string)
                if match is not None:
                    self.record(match)

            if 'PING' in line:
                msg = line.split(':')[1].lstrip().rstrip()
                self.client.sendall("PONG {0}\r\n".format(msg))
            
            if 'Nickname is already in use' in line:
                self.client.sendall("NICK %s\r\n" % self.config['nick']+str(random.randint(1, 10)))
                self.client.sendall("USER %s %s as :%s\r\n" % (self.config['ident'], self.host, self.config['realname']))
                self.client.send("JOIN :#%s\r\n" % self.channel)
    
    def record(self,match):
        url="http://en.wikipedia.org/wiki/%s" % unicode(match.group(1),"utf-8")
        match2=re.search("\x0302(.*)\x0310",match.group(6))
        if re.search("N|upload",match.group(2)):
            self.logger.info("NEW entry at URL: %s" % url)
            self._create_resource(self.config['uri_host']+url)
            if match2 is not None:
                url="http://en.wikipedia.org/wiki/%s" % unicode(match2.group(1),"utf-8")
                self.logger.info("New entry part 2 at URL: %s" % url)
                self._create_resource(url)
        elif re.search("delete",match.group(2)):    
            self.logger.info("DELETED URL: %s" % url)
            self._delete_resource(url)
            if match2 is not None:
                url="http://en.wikipedia.org/wiki/%s" % unicode(match2.group(1),"utf-8")
                self.logger.info("DELETED2 entry part2 at URL: %s" % url)
                self._delete_resource(url)
        elif re.search("approve|move",match.group(2)):
            self.logger.info("APPROVEDUpdate at URL: %s" % match.group(1))
            self._update_resource(url)
            if match2 is not None:
                url="http://en.wikipedia.org/wiki/%s" % unicode(match2.group(1),"utf-8")
                self.logger.info("APPROVED or Moved entry part 2 at URL: %s" % url)
                self._update_resource(url)
        else:
            self.logger.debug("Update at URL: %s" % match.group(1))
            self.logger.info("Normal UPDATE entry at URL: %s" % url)
            self._update_resource(url)
            
    def _log_stats(self):
        """Log current source statistics"""
        stats = {
            'no_resources': self.resource_count,
            'no_events': self.no_events
        }
        self.logger.info("Source stats: %s" % stats)
           
    def disconnect(self):
        if self.client is not None:
            self.client.disconnect()
             
    def __str__(self):
        """Prints out the source's resources"""
        return pprint.pformat(self._repository)