#!/usr/bin/env python
from __future__ import print_function
"""
oleobj.py

oleobj is a Python script and module to parse OLE objects and files stored
into various file formats such as RTF or MS Office documents (e.g. Word, Excel).

Author: Philippe Lagadec - http://www.decalage.info
License: BSD, see source code or documentation

oleobj is part of the python-oletools package:
http://www.decalage.info/python/oletools
"""

# === LICENSE ==================================================================

# oleobj is copyright (c) 2015-2017 Philippe Lagadec (http://www.decalage.info)
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


#------------------------------------------------------------------------------
# CHANGELOG:
# 2015-12-05 v0.01 PL: - first version
# 2016-06          PL: - added main and process_file (not working yet)
# 2016-07-18 v0.48 SL: - added Python 3.5 support
# 2016-07-19       PL: - fixed Python 2.6-7 support
# 2016-11-17 v0.51 PL: - fixed OLE native object extraction
# 2016-11-18       PL: - added main for setup.py entry point
# 2017-05-03       PL: - fixed absolute imports (issue #141)

__version__ = '0.51'

#------------------------------------------------------------------------------
# TODO:
# + setup logging (common with other oletools)


#------------------------------------------------------------------------------
# REFERENCES:

# Reference for the storage of embedded OLE objects/files:
# [MS-OLEDS]: Object Linking and Embedding (OLE) Data Structures
# https://msdn.microsoft.com/en-us/library/dd942265.aspx

# - officeparser: https://github.com/unixfreak0037/officeparser
# TODO: oledump


#--- IMPORTS ------------------------------------------------------------------

import logging, struct, optparse, os, re, sys

# IMPORTANT: it should be possible to run oletools directly as scripts
# in any directory without installing them with pip or setup.py.
# In that case, relative imports are NOT usable.
# And to enable Python 2+3 compatibility, we need to use absolute imports,
# so we add the oletools parent folder to sys.path (absolute+normalized path):
_thismodule_dir = os.path.normpath(os.path.abspath(os.path.dirname(__file__)))
# print('_thismodule_dir = %r' % _thismodule_dir)
_parent_dir = os.path.normpath(os.path.join(_thismodule_dir, '..'))
# print('_parent_dir = %r' % _thirdparty_dir)
if not _parent_dir in sys.path:
    sys.path.insert(0, _parent_dir)

from oletools.thirdparty.olefile import olefile
from oletools.thirdparty.xglob import xglob
from ppt_record_parser import is_ppt, PptFile, PptRecordExOleVbaActiveXAtom

# === LOGGING =================================================================

class NullHandler(logging.Handler):
    """
    Log Handler without output, to avoid printing messages if logging is not
    configured by the main application.
    Python 2.7 has logging.NullHandler, but this is necessary for 2.6:
    see https://docs.python.org/2.6/library/logging.html#configuring-logging-for-a-library
    """
    def emit(self, record):
        pass

def get_logger(name, level=logging.CRITICAL+1):
    """
    Create a suitable logger object for this module.
    The goal is not to change settings of the root logger, to avoid getting
    other modules' logs on the screen.
    If a logger exists with same name, reuse it. (Else it would have duplicate
    handlers and messages would be doubled.)
    The level is set to CRITICAL+1 by default, to avoid any logging.
    """
    # First, test if there is already a logger with the same name, else it
    # will generate duplicate messages (due to duplicate handlers):
    if name in logging.Logger.manager.loggerDict:
        #NOTE: another less intrusive but more "hackish" solution would be to
        # use getLogger then test if its effective level is not default.
        logger = logging.getLogger(name)
        # make sure level is OK:
        logger.setLevel(level)
        return logger
    # get a new logger:
    logger = logging.getLogger(name)
    # only add a NullHandler for this logger, it is up to the application
    # to configure its own logging:
    logger.addHandler(NullHandler())
    logger.setLevel(level)
    return logger

# a global logger object used for debugging:
log = get_logger('oleobj')

def enable_logging():
    """
    Enable logging for this module (disabled by default).
    This will set the module-specific logger level to NOTSET, which
    means the main application controls the actual logging level.
    """
    log.setLevel(logging.NOTSET)


# === CONSTANTS ==============================================================

# some str methods on Python 2.x return characters,
# while the equivalent bytes methods return integers on Python 3.x:
if sys.version_info[0] <= 2:
    # Python 2.x
    NULL_CHAR = '\x00'
else:
    # Python 3.x
    NULL_CHAR = 0


# === GLOBAL VARIABLES =======================================================

# struct to parse an unsigned integer of 32 bits:
struct_uint32 = struct.Struct('<L')
assert struct_uint32.size == 4  # make sure it matches 4 bytes

# struct to parse an unsigned integer of 16 bits:
struct_uint16 = struct.Struct('<H')
assert struct_uint16.size == 2  # make sure it matches 2 bytes

# max length of a zero-terminated ansi string. Not sure what this really is
STR_MAX_LEN = 1024

# === FUNCTIONS ==============================================================

def read_uint32(data, index):
    """
    Read an unsigned integer from the first 32 bits of data.

    :param data: bytes string or stream containing the data to be extracted.
    :param index: index to start reading from or None if data is stream.
    :return: tuple (value, index) containing the read value (int),
             and the index to continue reading next time.
    """
    if index is None:
        value = struct_uint32.unpack(data.read(4))[0]
    else:
        value = struct_uint32.unpack(data[index:index+4])[0]
        index += 4
    return (value, index)


def read_uint16(data, index):
    """
    Read an unsigned integer from the 16 bits of data following index.

    :param data: bytes string or stream containing the data to be extracted.
    :param index: index to start reading from or None if data is stream
    :return: tuple (value, index) containing the read value (int),
             and the index to continue reading next time.
    """
    if index is None:
        value = struct_uint16.unpack(data.read(2))[0]
    else:
        value = struct_uint16.unpack(data[index:index+2])[0]
        index += 2
    return (value, index)


def read_LengthPrefixedAnsiString(data, index):
    """
    Read a length-prefixed ANSI string from data.

    :param data: bytes string or stream containing the data to be extracted.
    :param index: index in data where string size start or None if data is stream
    :return: tuple (value, index) containing the read value (bytes string),
             and the index to start reading from next time.
    """
    length, index = read_uint32(data, index)
    # if length = 0, return a null string (no null character)
    if length == 0:
        return ('', index)
    # extract the string without the last null character
    if index is None:
        ansi_string = data.read(length-1)
        null_char = data.read(1)
    else:
        ansi_string = data[index:index+length-1]
        null_char = data[index+length]
        index += length
    # TODO: only in strict mode:
    # check the presence of the null char:
    assert null_char == NULL_CHAR
    return (ansi_string, index)


def read_zero_terminated_ansi_string(data, index):
    """
    Read a zero-terminated ANSI string from data

    Guessing that max length is 256 bytes.

    :param data: bytes string or stream containing an ansi string
    :param index: index at which the string should start or None if data is stream
    :return: tuple (string, index) containing the read string (bytes string),
             and the index to start reading from next time.
    """
    if index is None:
        result = []
        for count in xrange(STR_MAX_LEN):
            char = data.read(1)
            if char == b'\x00':
                return b''.join(result), index
            result.append(char)
        raise ValueError('found no string-terminating zero-byte!')
    else:       # data is byte array, can just search
        end_idx = data.index(b'\x00', index, index+STR_MAX_LEN)
        return data[index:end_idx], end_idx+1   # return index after the 0-byte


# === CLASSES ================================================================

class OleNativeStream (object):
    """
    OLE object contained into an OLENativeStream structure.
    (see MS-OLEDS 2.3.6 OLENativeStream)
    """
    # constants for the type attribute:
    # see MS-OLEDS 2.2.4 ObjectHeader
    TYPE_LINKED = 0x01
    TYPE_EMBEDDED = 0x02


    def __init__(self, bindata=None, package=False):
        """
        Constructor for OleNativeStream.
        If bindata is provided, it will be parsed using the parse() method.

        :param bindata: forwarded to parse, see docu there
        :param package: bool, set to True when extracting from an OLE Package
                        object
        """
        self.filename = None
        self.src_path = None
        self.unknown_short = None
        self.unknown_long_1 = None
        self.unknown_long_2 = None
        self.temp_path = None
        self.actual_size = None
        self.data = None
        self.package = package
        if bindata is not None:
            self.parse(data=bindata)

    def parse(self, data):
        """
        Parse binary data containing an OLENativeStream structure,
        to extract the OLE object it contains.
        (see MS-OLEDS 2.3.6 OLENativeStream)

        :param data: bytes array or stream, containing OLENativeStream
                     structure containing an OLE object
        :return: None
        """
        # TODO: strict mode to raise exceptions when values are incorrect
        # (permissive mode by default)
        if hasattr(data, 'read'):
            index = None       # marker for read_* functions to expect stream
        else:
            index = 0          # marker for read_* functions to expect array

        # An OLE Package object does not have the native data size field
        if not self.package:
            self.native_data_size, index = read_uint32(data, index)
            log.debug('OLE native data size = {0:08X} ({0} bytes)'
                      .format(self.native_data_size))
        # I thought this might be an OLE type specifier ???
        self.unknown_short, index = read_uint16(data, index)
        self.filename, index = read_zero_terminated_ansi_string(data, index)
        # source path
        self.src_path, index = read_zero_terminated_ansi_string(data, index)
        # TODO I bet these next 8 bytes are a timestamp => FILETIME from olefile
        self.unknown_long_1, index = read_uint32(data, index)
        self.unknown_long_2, index = read_uint32(data, index)
        # temp path?
        self.temp_path, index = read_zero_terminated_ansi_string(data, index)
        # size of the rest of the data
        try:
            self.actual_size, index = read_uint32(data, index)
            if index is None:     # data is a bytes stream
                self.data = data
            else:                 # data is a bytes array
                self.data = data[index:index+self.actual_size]
            # TODO: exception when size > remaining data
            # TODO: SLACK DATA
        except IOError, struct.error:      # no data to read actual_size
            logging.debug('data is not embedded but only a link')
            self.actual_size = 0
            self.data = None


class OleObject (object):
    """
    OLE 1.0 Object

    see MS-OLEDS 2.2 OLE1.0 Format Structures
    """

    # constants for the format_id attribute:
    # see MS-OLEDS 2.2.4 ObjectHeader
    TYPE_LINKED = 0x01
    TYPE_EMBEDDED = 0x02


    def __init__(self, bindata=None):
        """
        Constructor for OleObject.
        If bindata is provided, it will be parsed using the parse() method.

        :param bindata: bytes, OLE 1.0 Object structure containing an OLE object
        """
        self.ole_version = None
        self.format_id = None
        self.class_name = None
        self.topic_name = None
        self.item_name = None
        self.data = None
        self.data_size = None

    def parse(self, data):
        """
        Parse binary data containing an OLE 1.0 Object structure,
        to extract the OLE object it contains.
        (see MS-OLEDS 2.2 OLE1.0 Format Structures)

        :param data: bytes, OLE 1.0 Object structure containing an OLE object
        :return:
        """
        # from ezhexviewer import hexdump3
        # print("Parsing OLE object data:")
        # print(hexdump3(data, length=16))
        # Header: see MS-OLEDS 2.2.4 ObjectHeader
        self.ole_version, index = read_uint32(data, index)
        self.format_id, index = read_uint32(data, index)
        log.debug('OLE version=%08X - Format ID=%08X' % (self.ole_version, self.format_id))
        assert self.format_id in (self.TYPE_EMBEDDED, self.TYPE_LINKED)
        self.class_name, index = read_LengthPrefixedAnsiString(data, index)
        self.topic_name, index = read_LengthPrefixedAnsiString(data, index)
        self.item_name, index = read_LengthPrefixedAnsiString(data, index)
        log.debug('Class name=%r - Topic name=%r - Item name=%r'
                      % (self.class_name, self.topic_name, self.item_name))
        if self.format_id == self.TYPE_EMBEDDED:
            # Embedded object: see MS-OLEDS 2.2.5 EmbeddedObject
            #assert self.topic_name != '' and self.item_name != ''
            self.data_size, index = read_uint32(data, index)
            log.debug('Declared data size=%d - remaining size=%d' % (self.data_size, len(data)-index))
            # TODO: handle incorrect size to avoid exception
            self.data = data[index:index+self.data_size]
            assert len(self.data) == self.data_size
            self.extra_data = data[index+self.data_size:]



def sanitize_filename(filename, replacement='_', max_length=200):
    """compute basename of filename. Replaces all non-whitelisted characters.
       The returned filename is always a basename of the file."""
    basepath = os.path.basename(filename).strip()
    sane_fname = re.sub(r'[^\w\.\- ]', replacement, basepath)

    while ".." in sane_fname:
        sane_fname = sane_fname.replace('..', '.')

    while "  " in sane_fname:
        sane_fname = sane_fname.replace('  ', ' ')

    if not len(filename):
        sane_fname = 'NONAME'

    # limit filename length
    if max_length:
        sane_fname = sane_fname[:max_length]

    return sane_fname


def find_ole_in_ppt(filename):
    """ find ole streams in ppt """
    for stream in PptFile(filename).iter_streams():
        for record in stream.iter_records():
            if isinstance(record, PptRecordExOleVbaActiveXAtom):
                ole = None
                try:
                    data_start = next(record.iter_uncompressed())
                    if data_start[:len(olefile.MAGIC)] != olefile.MAGIC:
                        continue   # could be an ActiveX control or VBA Storage

                    # otherwise, this should be an OLE object
                    ole = record.get_data_as_olefile()
                    yield ole
                except IOError:
                    logging.warning('Error reading data from {0} stream or '
                                    'interpreting it as OLE object'
                                    .format(stream.name), exc_info=True)
                finally:
                    if ole is not None:
                        ole.close()


def find_ole(filename, data):
    """ try to open somehow as zip/ole/rtf/... ; yield None if fail

    if data is given, filename is ignored
    """

    try:
        if data is not None:
            # assume data is a complete OLE file
            logging.info('working on raw OLE data (filename: {0})'
                         .format(filename))
            yield olefile.OleFileIO(data)
        elif olefile.isOleFile(filename):
            if is_ppt(filename):
                logging.info('is ppt file: ' + filename)
                for ole in find_ole_in_ppt(filename):
                    yield ole
                    ole.close()
            else:
                logging.info('is ole file: ' + filename)
                ole = olefile.OleFileIO(filename)
                yield ole
                ole.close()
        elif is_zipfile(filename):
            logging.info('is zip file: ' + filename)
            zipper = ZipFile(filename, 'r')
            for subfile in zipper.namelist():
                head = b''
                try:
                    with zipper.open(subfile) as file_handle:
                        head = file_handle.read(len(olefile.MAGIC))
                except RuntimeError:
                    logging.error('zip is encrypted: ' + filename)
                    yield None
                    continue

                if head == olefile.MAGIC:
                    logging.info('  unzipping ole: ' + subfile)
                    with zipper.open(subfile) as file_handle:
                        ole = olefile.OleFileIO(file_handle)
                        yield ole
                        ole.close()
                else:
                    logging.debug('unzip skip: ' + subfile)
        else:
            logging.warning('open failed: ' + filename)
            yield None   # --> leads to non-0 return code
    except Exception:
        logging.error('Caught exception opening {0}'.format(filename),
                      exc_info=True)
        yield None   # --> leads to non-0 return code but try next file first


def process_file(container, filename, data, output_dir=None):
    """ find embedded objects in given file

    if data is given (from xglob for encrypted zip files), then filename is
    not used for reading. If not (usual case), then data is read from filename
    on demand.

    If output_dir is given and does not exist, it is created. If it is not
    given, data is saved to same directory as the input file.
    """
    if output_dir:
        if not os.path.isdir(output_dir):
            log.info('creating output directory %s' % output_dir)
            os.mkdir(output_dir)

        fname_prefix = os.path.join(output_dir,
                                    sanitize_filename(filename))
    else:
        base_dir = os.path.dirname(filename)
        sane_fname = sanitize_filename(filename)
        fname_prefix = os.path.join(base_dir, sane_fname)

    # TODO: option to extract objects to files (false by default)
    print ('-'*79)
    print ('File: %r' % filename)
    index = 1

    # look for ole files inside file (e.g. unzip docx)
    flag_no_ole = False
    for ole in find_ole(filename, data):
        if ole is None:    # no ole file found
            flag_no_ole = True
            continue

        for stream in ole.listdir():
            if stream[-1] == '\x01Ole10Native':
                process_native_stream(ole, stream, fname_prefix, index)
                index += 1


def process_native_stream(ole, stream, fname_prefix, index):
    """ Dump data from OLE embedded object stream """
    objdata = ole.openstream(stream).read()
    stream_path = '/'.join(stream)
    log.debug('Checking stream %r' % stream_path)
    try:
        print('extract file embedded in OLE object from stream %r:' % stream_path)
        print ('Parsing OLE Package')
        opkg = OleNativeStream(bindata=objdata)
        print ('Filename = %r' % opkg.filename)
        print ('Source path = %r' % opkg.src_path)
        print ('Temp path = %r' % opkg.temp_path)
        if opkg.filename:
            fname = '%s_%s' % (fname_prefix,
                               sanitize_filename(opkg.filename))
        else:
            fname = '%s_object_%03d.noname' % (fname_prefix, index)
        print ('saving to file %s' % fname)
        open(fname, 'wb').write(opkg.data)
    except Exception:
        log.debug('*** Not an OLE 1.0 Object')


#=== MAIN =================================================================

def main():
    # print banner with version
    print ('oleobj %s - http://decalage.info/oletools' % __version__)
    print ('THIS IS WORK IN PROGRESS - Check updates regularly!')
    print ('Please report any issue at https://github.com/decalage2/oletools/issues')
    print ('')

    DEFAULT_LOG_LEVEL = "warning" # Default log level
    LOG_LEVELS = {'debug':    logging.DEBUG,
              'info':     logging.INFO,
              'warning':  logging.WARNING,
              'error':    logging.ERROR,
              'critical': logging.CRITICAL
             }

    usage = 'usage: %prog [options] <filename> [filename2 ...]'
    parser = optparse.OptionParser(usage=usage)
    # parser.add_option('-o', '--outfile', dest='outfile',
    #     help='output file')
    # parser.add_option('-c', '--csv', dest='csv',
    #     help='export results to a CSV file')
    parser.add_option("-r", action="store_true", dest="recursive",
        help='find files recursively in subdirectories.')
    parser.add_option("-d", type="str", dest="output_dir",
        help='use specified directory to output files.', default=None)
    parser.add_option("-z", "--zip", dest='zip_password', type='str', default=None,
        help='if the file is a zip archive, open first file from it, using the provided password (requires Python 2.6+)')
    parser.add_option("-f", "--zipfname", dest='zip_fname', type='str', default='*',
        help='if the file is a zip archive, file(s) to be opened within the zip. Wildcards * and ? are supported. (default:*)')
    parser.add_option('-l', '--loglevel', dest="loglevel", action="store", default=DEFAULT_LOG_LEVEL,
                            help="logging level debug/info/warning/error/critical (default=%default)")

    # options for compatibility with ripOLE
    parser.add_option('-i', '--more-input', type='str', default=None,
                      help='Additional file to parse (same as positional arguments)')
    parser.add_option('-v', '--verbose', action='store_true',
                      help='verbose mode, set logging to DEBUG (overwrites -l)')

    (options, args) = parser.parse_args()
    if options.more_input:
        args += [options.more_input, ]
    if options.verbose:
        options.loglevel = 'debug'

    # Print help if no arguments are passed
    if len(args) == 0:
        print (__doc__)
        parser.print_help()
        sys.exit()

    # Setup logging to the console:
    # here we use stdout instead of stderr by default, so that the output
    # can be redirected properly.
    logging.basicConfig(level=LOG_LEVELS[options.loglevel], stream=sys.stdout,
                        format='%(levelname)-8s %(message)s')
    # enable logging in the modules:
    log.setLevel(logging.NOTSET)


    for container, filename, data in xglob.iter_files(args, recursive=options.recursive,
        zip_password=options.zip_password, zip_fname=options.zip_fname):
        # ignore directory names stored in zip files:
        if container and filename.endswith('/'):
            continue
        process_file(container, filename, data, options.output_dir)

if __name__ == '__main__':
    main()

