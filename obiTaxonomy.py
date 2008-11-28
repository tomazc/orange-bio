import urllib2
import os, sys, shutil
import cPickle
import tarfile
import StringIO
import obiGenomicsUpdate
import obiData
import orngEnviron

from urllib2 import urlopen
from collections import defaultdict

default_database_path = os.path.join(orngEnviron.bufferDir, "bigfiles", "Taxonomy")

class MultipleSpeciesException(Exception):
    pass

class UnknownSpeciesIdentifier(Exception):
    pass

def cached(func):
    """Cached one arg method
    """
    def f(self, arg):
        if arg not in self._cache:
            self._cache[arg] = func(self, arg)
        return self._cache[arg]
    f._cache = {}
    f.__name__ = "Cached " + func.__name__
    return f
    
class TextDB(object):
    entry_start_string = chr(255)
    entry_end_string = chr(254)+"\n"
    entry_separator_string = chr(253)
    
    @property
    def _text_lower(self):
        if id(self._text) == self._lower_text_id:
            return self._lower_text
        else:
            self._lower_text_id = id(self._text)
            self._lower_text = self._text.lower()
            return self._lower_text
        
    def __init__(self, file=None, **kwargs):
        self._text = ""
        self._lower_text_id = id(self._text) - 1
        self._cache = {}
        self.__dict__.update(kwargs)
        
        if file != None:
            self._text = open(file, "rb").read()

    def _find_all(self, string, start=0, text=None, unique=True):
        text = text if text != None else self._text_lower
        while True:
            index = text.find(string, start)
            if index != -1:
                yield index
                if unique:
                    start = text.find(self.entry_start_string, index + 1)
                else:
                    start = index + 1
            else:
                raise StopIteration

    def _get_entry_at(self, index, text=None):
        text = text if text != None else self._text
        start = text.rfind(self.entry_start_string, 0, index + 1)
        end = text.find(self.entry_end_string, index)
        return self._text[start+1:end]

    @cached
    def get_entry(self, id):
        try:
            index = self._find_all(self.entry_start_string + id + self.entry_separator_string).next()
        except StopIteration:
            raise KeyError, id
        return self._get_entry_at(index)
                
    def search(self, string):
        string = string.lower()
        res = []
        for idx in self._find_all(string):
            entry = self._get_entry_at(idx)
            id , rest = entry.split(self.entry_separator_string, 1)
            self._cache[id] = entry
            res.append(id)
        return res

    def insert(self, entry):
        self._text += self.entry_start_string + self.entry_separator_string.join(entry) + self.entry_end_string

    def __iter__(self):
        for idx in self._find_all(self.entry_start_string):
            entry = self._get_entry_at(idx)
            if entry:
                yield entry.split(self.entry_separator_string ,1)[0]

    def __getitem__(self, id):
        entry = self.get_entry(id)
        return entry.split(self.entry_separator_string)[1:]

    def __setitem__(self, id, entry):
        self.insert([id] + list(entry))

    def create(self, filename):
        f = open(filename, "wb")
        f.write(self._text)
        def write(entry):
            f.write(self.entry_start_string + self.entry_separator_string.join(entry) + self.entry_end_string)
        return write
    
class Taxonomy(object):
    __shared_state = {"_text":None, "_info":None}
    def __init__(self):
        self.__dict__ = self.__shared_state
        if not self._text:
            self.Load()
            
    def Load(self):
        try:
            self._text = TextDB(os.path.join(default_database_path, "ncbi_taxonomy.tar.gz", "ncbi_taxonomy.db"))
            self._info = TextDB(os.path.join(default_database_path, "ncbi_taxonomy.tar.gz", "ncbi_taxonomy_inf.db"))
            return
        except Exception, ex:
            print >> sys.stderr, ex, "Could not load taxonomy from local cache\nAttempting to download from server."
        try:
            import orngServerFiles as sf
            sf.download("Taxonomy", "ncbi_taxonomy.tar.gz")
            self._text = TextDB(os.path.join(default_database_path, "ncbi_taxonomy.tar.gz", "ncbi_taxonomy.db"))
            self._info = TextDB(os.path.join(default_database_path, "ncbi_taxonomy.tar.gz", "ncbi_taxonomy_inf.db"))
            return
        except Exception, ex:
            print >> sys.stderr, ex
            raise
                
    def search(self, string, onlySpecies=True):
        res = self._text.search(string)
        if onlySpecies:
            res = [r for r in res if "species" in self._text[r][1]]
        return res

    def __iter__(self):
        return iter(self._text)

    def __getitem__(self, id):
        try:
            entry = self._text[id]
        except KeyError:
            raise UnknownSpiciesIdentifier
        return entry[2] ## item with index 2 is allways scientific name

    def other_names(self, id):
        try:
            entry = self._text[id]
        except KeyError:
            raise UnknownSpiciesIdentifier
        info = self._info[id]
        names = entry[2:] ## indes 2 and larger are names
        return list(zip(names, info))[1:] ## exclude scientific name
        
        
    @staticmethod
    def ParseTaxdumpFile(file=None, outputdir=None, callback=None):
        from cStringIO import StringIO
        if file == None:
            file = tarfile.open(None, "r:gz", StringIO(urlopen("ftp://ftp.ncbi.nih.gov/pub/taxonomy/taxdump.tar.gz").read()))
        if type(file) == str:
            file = tarfile.open(file)
        names = file.extractfile("names.dmp").readlines()
        nodes = file.extractfile("nodes.dmp").readlines()
        namesDict = defaultdict(list)
        for line in names:
            if not line.strip():
                continue
            line = line.rstrip("\t\n|").split("\t|\t")
            id, name, unique_name, name_class = line
            if unique_name:
                namesDict[id].append((unique_name , name_class))
            else:
                namesDict[id].append((name , name_class))

        nodesDict = {}
        for line in nodes:
            if not line.strip():
                continue
            line = line.split("\t|\t")[:3]
            id, parent, rank = line
            nodesDict[id] = (parent, rank)
        
        name_class_codes = defaultdict(iter(range(255)).next)
        name_class_codes["scientific name"] ## Force scientific name to be first
        if outputdir == None:
            outputdir = default_database_path
        text = TextDB().create(os.path.join(outputdir, "ncbi_taxonomy.db"))
        info = TextDB().create(os.path.join(outputdir, "ncbi_taxonomy_inf.db"))
        milestones = set(range(0, len(namesDict), max(len(namesDict)/100, 1)))
        for i, (id, names) in enumerate(namesDict.items()):
            parent, rank = nodesDict[id]
            ## id, parent and rank go first
            entry = [id, parent, rank]
            ## all names and name class codes pairs follow ordered so scientific name is first
            names = sorted(names, key=lambda (name, class_): name_class_codes[class_])
            entry.extend([name for name ,class_ in names])
            info_entry = [id] + [class_ for name, class_ in names]
            text(entry)
            info(info_entry)
            if callback and i in milestones:
                callback(i)
    
def name(taxid):
    return Taxonomy()[taxid]

def other_names(taxid):
    return  Taxonomy().other_names(taxid)

def search(string, onlySpecies=True):
    return Taxonomy().search(string, onlySpecies)

import obiGenomicsUpdate

class Update(obiGenomicsUpdate.Update):
    def GetDownloadable(self):
        return [Update.UpdateTaxonomy]
    
    def IsUpdatable(self, func, args):
        from datetime import datetime
        import obiData
        if func == Update.UpdateTaxonomy:
##            stream = urllib2.urlopen("ftp://ftp.ncbi.nih.gov/pub/taxonomy/taxdump.tar.gz")
##            date = datetime.strptime(stream.headers.get("Last-Modified"), "%a, %d %b %Y %H:%M:%S %Z")
            ftp = obiData.FtpWorker("ftp.ncbi.nih.gov")
            size, date = ftp.statFtp("pub/taxonomy/taxdump.tar.gz")
            return date > self.GetLastUpdateTime(func, args)

    def UpdateTaxonomy(self):
        Taxonomy.ParseTaxdumpFile(outputdir=self.local_database_path)
        import tarfile
        tFile = tarfile.open(os.path.join(self.local_database_path, "ncbi_taxonomy.tar.gz"), "w:gz")
        tFile.add(os.path.join(self.local_database_path, "ncbi_taxonomy.db"), "ncbi_taxonomy.db")
        tFile.add(os.path.join(self.local_database_path, "ncbi_taxonomy_inf.db"), "ncbi_taxonomy_inf.db")
        tFile.close()

if __name__ == "__main__":
    ids = search("Homo sapiens")
    print ids
    print other_names(ids[0])
    