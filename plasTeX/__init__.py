#!/usr/bin/env python

import string, re
from DOM import Element, Text, Node, DocumentFragment, Document
from Tokenizer import Token
from plasTeX.Logging import getLogger

log = getLogger()
status = getLogger('status')
deflog = getLogger('parse.definitions')
envlog = getLogger('parse.environments')
mathshiftlog = getLogger('parse.mathshift')
digestlog = getLogger('parse.digest')

#
# Utility functions
#

def subclasses(o):
    output = [o]
    for item in o.__subclasses__():
        output.extend(subclasses(item))
    return output

def sourcechildren(o): 
    if o.childNodes:
        return u''.join([x.source for x in o.childNodes])
    return u''

def sourcearguments(o): 
    return o.argsource

def ismacro(o): 
    return hasattr(o, 'macroName')

def issection(o): 
    return level > Node.DOCUMENT_LEVEL and level < Node.ENVIRONMENT_LEVEL 

def macroname(o):
     if o.macroName is None:
         if type(o) is type:
             return o.__name__
         return type(o).__name__
     return o.macroName

from Config import config
def filenames():
    # Get the template and extension for output filenames
    ext = config['filenames']['extension']
    template = config['filenames'].get('template', raw=True) + ext

    # Return the index filename on the first pass
    yield config['filenames'].get('index', raw=True) + ext

    # Generate new filenames
    v = {'num':1}
    while 1:
        yield template % v
        v['num'] += 1
filenames = filenames()

class cachedproperty(object):
    """ Property that caches it's value for subsequent calls """
    def __init__(self, func, mode='w', cachedname='@%s'):
        self._func = func
        self._name = cachedname % func.func_name
        self._func_name = func.func_name
        self._readonly = 'w' not in mode
    def __set__(self, obj, value):
        if self._readonly:
            raise AttributeError, 'can\'t set attribute "%s"' % self._func_name
        setattr(obj, self._name)
    def __delete__(self, obj):
        if self._readonly:
            raise AttributeError, 'can\'t delete attribute "%s"' % self._func_name
        delattr(obj, self._name)
    def __get__(self, obj, type=None):
        if obj is None:
            return self
        try:
            value = getattr(obj, self._name)
        except AttributeError:
            value = self._func(obj)
            setattr(obj, self._name, value)
        return value

class Argument(object):
    """ 
    Macro argument

    Argument strings in macros are compiled into Arguments
    once.  Then the compiled arguments can be used to get the 
    arguments thereafter.

    """
    def __init__(self, name, options={}):
        self.name = name
        self.source = ''
        self.options = options.copy()

    def __repr__(self):
        return '%s: %s' % (self.name, self.options)

    def __cmp__(self, other):
        c = cmp(self.name, other.name)
        if c: return c
        return cmp(self.options, other.options)


class CSSStyles(dict):
    """ CSS Style object """
    def inline(self):
        """ 
        Create an inline style representation

        Returns:
        string containing inline CSS

        """
        if not self:
            return None      
        return u'; '.join([u'%s:%s' % (x[0],x[1]) for x in self.items()])
    inline = property(inline)

class Macro(Element):
    """
    Base class for all macros

    """
    MODE_NONE = 0
    MODE_BEGIN = 1
    MODE_END = 2

    macroName = None        # TeX macro name (instead of class name)
    macroMode = MODE_NONE   # begin, end, or none
    categories = None       # category codes local to this macro

    # Node variables
    level = Node.COMMAND_LEVEL
    nodeType = Node.ELEMENT_NODE
    nodeValue = None

    # Counter associated with this macro
    counter = None

    # Value to return when macro is referred to by \ref
    ref = None

    # Source of the TeX macro arguments
    argsource = ''

    def __init__(self, *args, **kwargs):
        Element.__init__(self, *args, **kwargs)
        self.style = CSSStyles()

    def url(self):
        if self.filename:
            return self.filename
        return '%s#%s' % (self.filename, self.id)
    url = property(url)

    def filename(self):
        try:
            return getattr(self, '@filename')
        except:
            if self.level > 10:
                filename = None
            if self.id == id(self):
                filename = filenames.next()
            else:
                filename = '%s.html' % self.id
            setattr(self, '@filename', filename)
        return filename
    filename = property(filename)

    def locals(self):
        """ Retrieve all macros local to this namespace """
        tself = type(self)
        localsname = '@locals'
        # Check for cached versions first
        if vars(tself).has_key(localsname):
            return getattr(tself, localsname)
        mro = list(tself.__mro__)
        mro.reverse()
        loc = {}
        for cls in mro:
            for value in vars(cls).values():
                if ismacro(value):
                    loc[macroname(value)] = value
        # Cache the locals in a unique name
        setattr(tself, localsname, loc)
        return loc

    def id():
        def fset(self, value):
            if value:
                setattr(self, '@id', value)
            else:
                delattr(self, '@id')
        def fget(self):
            return getattr(self, '@id', id(self))
        return locals()
    id = property(**id())

    def invoke(self, tex):
        # Just pop the context if this is a \end token
        if self.macroMode == Macro.MODE_END:
            tex.context.pop(self)
            return

        # If this is a \begin token or the element needs to be
        # closed automatically (i.e. \section, \item, etc.), just 
        # push the new context and return the instance.
        elif self.macroMode == Macro.MODE_BEGIN:
            tex.context.push(self)
            self.parse(tex)
            return

        # Push, parse, and pop.  The command doesn't need to stay on
        # the context stack.  We push an empty context so that the
        # `self' token doesn't get put into the output stream twice
        # (once here and once with the pop).
        tex.context.push(self)
        self.parse(tex)
        tex.context.pop(self)

    def tagName(self):
        t = type(self)
        if t.macroName is None:
            return t.__name__
        return t.macroName
    nodeName = tagName = property(tagName)

    def source(self):
        name = self.tagName

        # Automatically revert internal names like "active::~"
        escape = '\\'
        if name.count('::'):
            name = name.split('::').pop()
            escape = ''

        # \begin environment
        # If self.childNodes is not empty, print out the entire environment
        if self.macroMode == Macro.MODE_BEGIN:
            argsource = sourcearguments(self)
            if not argsource: 
                argsource = ' '
            s = '%sbegin{%s}%s' % (escape, name, argsource)
            if self.childNodes:
                s += '%s%send{%s}' % (sourcechildren(self), escape, name)
            return s

        # \end environment
        if self.macroMode == Macro.MODE_END:
            return '%send{%s}' % (escape, name)

        argsource = sourcearguments(self)
        if not argsource: 
            argsource = ' '
        s = '%s%s%s' % (escape, name, argsource)

        # If self.childNodes is not empty, print out the contents
        if self.attributes and self.attributes.has_key('self'):
            pass
        else:
            if self.childNodes:
                s += sourcechildren(self)
        return s

    source = property(source)

    def parse(self, tex): 
        """ 
        Parse the arguments defined in the `args` variable 

        Required Arguments:
        tex -- the TeX stream to parse from

        Returns:
        self.attributes

        """
        if self.macroMode == Macro.MODE_END:
            return
        self.argsource = ''
        arg = None
        try:
            for i, arg in enumerate(self.arguments):
                output, source = tex.readArgumentAndSource(**arg.options)
                # Check for a '*' type argument at the beginning of the
                # argument list.  If there is one, don't increment counters
                # or set labels.  This must be done immediately since
                # the following arguments may contain labels.
                if i == 0:
                    if arg.name == '*modifier*':
                        if output is None:
                            self.resolve(tex)
                    else:
                        self.resolve(tex)
                self.argsource += source
                self.attributes[arg.name] = output
        except:
            raise
            log.error('Error while parsing argument "%s" of "%s"' % (arg.name, self.nodeName))
        return self.attributes

    def resolve(self, tex):
        """
        Set macro up for labelling, increment counters, etc.

        Required Arguments:
        tex -- the TeX instance containing the current context

        """
        if self.counter:
            tex.context.currentlabel = self
            try:
                Counter.counters[self.counter].stepcounter()
            except KeyError:
                log.warning('Could not find counter "%s"', self.counter)
                tex.context.newcounter(self.counter,1)
            self.ref = tex.expandtokens(tex.context['the'+self.counter].invoke(tex))

    def arguments(self):
        """ 
        Compile the argument string into function call arguments 

        Returns:
        arguments as compiled entities

        """
        tself = type(self)

        if vars(tself).has_key('@arguments'):
            return vars(tself)['@arguments']

        if not(getattr(tself, 'args', None)):
            setattr(tself, '@arguments', [])
            return getattr(tself, '@arguments')

        # Split the arguments into their primary components
        args = iter([x.strip() for x in 
                     re.split(r'(\w+(?::\w+(?:\(\S\))?(?::\w+)?)?|\W|\s+)', 
                              tself.args) if x is not None and x.strip()])

        groupings = {'[':'[]','(':'()','<':'<>','{':'{}'}

        macroargs = []
        argdict = {}
        for item in args:

            # Modifier argument
            if item in '*+-':
                if argdict:
                    raise ValueError, \
                        'Improperly placed "%s" in argument string "%s"' % \
                        (item, tself.args)
                argdict.clear()
                macroargs.append(Argument('*modifier*', {'spec':item}))

            # Optional equals
            elif item in '=':
                argdict.clear()
                macroargs.append(Argument('*equals*', {'spec':item}))

            # Beginning of group
            elif item in '[(<{':
                argdict.clear()
                argdict['spec'] = groupings[item]

            # End of group
            elif item in '])>}':
                pass

            # Argument name (and possibly type)
            elif item[0] in string.letters:
                parts = item.split(':')
                item = parts.pop(0)
                # Parse for types and subtypes
                if parts: 
                    # We already have a type, so check for subtypes
                    # for list items
                    if argdict.has_key('type'):
                        argdict['subtype'] = parts.pop(0)
                    else:
                        # Split type and possible delimiter
                        argdict['type'], argdict['delim'] = re.search(r'(\w+)(?:\((\W)\))?', parts.pop(0)).groups()
                        if parts:
                            argdict['subtype'] = parts.pop(0)
                # Arguments that are instance variables are always expanded
                if argdict.get('type') in ['cs','nox']:
                    argdict['expanded'] = False
                else:
                    argdict['expanded'] = True
                macroargs.append(Argument(item, argdict))
                argdict.clear()

            else:
                raise ValueError, 'Could not parse argument string "%s", reached unexpected "%s"' % (tself.args, item)

        setattr(tself, '@arguments', macroargs)

        return macroargs

    arguments = property(arguments)

    def digestUntil(self, tokens, endclass):
        """
        Absorb tokens until a token of the given class is given

        This method is useful for things like lists and tables 
        when one element is actually ended by the occurrence of 
        another (i.e. \\item ended by \\item, array cell ended by 
        array cell, array cell ended by array row, etc.).

        Required Arguments:
        tokens -- iterator of tokens in the stream
        endclass -- class reference or tuple of class references
            that, when a token of that type is reached, stops
            the digestion process

        Returns:
        None -- if the context ended without reaching a token of
            the requested type
        token -- the token of the requested type if it was found

        """
        for tok in tokens:
            if tok.nodeType == Node.ELEMENT_NODE:
                if isinstance(tok, endclass):
                    tokens.push(tok)
                    return tok
                tok.digest(tokens)
            if tok.contextDepth < self.contextDepth:
                tokens.push(tok)
                break
            self.appendChild(tok)
        
    def paragraphs(self):
        """
        Group content into paragraphs

        """
        parclass = None
        contentstart = None
        currentpar = None
    
        for i in range(len(self)-1, -1, -1):
    
            item = self[i]
    
            if item.nodeType == Node.ELEMENT_NODE and item.nodeName == 'par':
    
                if parclass is None:
                    parclass = type(item)
    
                # We don't have a paragraph yet, but we have some
                # content that belongs in a paragraph, so make one...
                if currentpar is None and contentstart is not None:
                    currentpar = parclass()
                    self.insert(contentstart+1, currentpar)
    
                # We don't have any paragraph content yet
                if contentstart is None:
                    currentpar = item
                    continue

                # Move contents from self into the paragraph
                for j in range(contentstart, i, -1):
                    currentpar.insert(0, self.pop(j))

                contentstart = None
                currentpar = item
                            
            # Found paragraph content
            elif item.level > Node.PAR_LEVEL and contentstart is None:
                contentstart = i
    
        # We hit the end of the content, so it needs to be absorbed
        if contentstart is not None and currentpar is not None:
            for j in range(contentstart, -1, -1):
                currentpar.insert(0, self.pop(j))

        try:
            first = self[0]
            if first.nodeType == Node.ELEMENT_NODE and first.nodeName == 'par':
                whitespace = True
                for item in first:
                    if item.isElementContentWhitespace:
                        continue
                    whitespace = False
                    break
                if whitespace:
                    self.pop(0)
        except IndexError: pass 


class TeXFragment(DocumentFragment):
    def source(self):
        return sourcechildren(self)
    source = property(source)

class TeXDocument(Document):
    def preamble(self):
        output = TeXFragment()
        for item in self:
            if item.nodeName == 'document':
                break
            output.append(item)
        return output
    preamble = property(preamble)

    def source(self):
        return sourcechildren(self)
    source = property(source)

class Command(Macro): 
    """ Base class for all Python-based LaTeX commands """

class Environment(Macro): 
    """ Base class for all Python-based LaTeX environments """
    level = Node.ENVIRONMENT_LEVEL

    def invoke(self, tex):
        if self.macroMode == Macro.MODE_END:
            tex.context.pop(self)
            return
        tex.context.push(self)
        self.parse(tex)

    def digest(self, tokens):
        if self.macroMode == Macro.MODE_END:
            return
        # Absorb the tokens that belong to us
        for item in tokens:
            if item.nodeType == Node.ELEMENT_NODE:
                if item.macroMode == Macro.MODE_END and type(item) is type(self):
                    break
                item.digest(tokens)
            if item.contextDepth < self.contextDepth:
                tokens.push(item)
                break
            self.appendChild(item)

class StringCommand(Command):
    """ 
    Convenience class for macros that are simply strings

    This class is used for simple macros that simply contain strings.

    Example::
        class figurename(StringCommand): value = 'Figure'
        class tablename(StringCommand): value = 'Table'

    """
    value = ''
    def invoke(self, tex): 
        return tex.texttokens(type(self).value)
                
class UnrecognizedMacro(Macro):
    """
    Base class for unrecognized macros

    When an unrecognized macro is requested, an instance of this 
    class is generated as a placeholder for the missing macro.

    """

class NewIf(Macro):
    """ Base class for all generated \\newifs """

    state = False

    def invoke(self, tex):
        return tex.readIfContent(type(self).state)

    def setState(cls, state):
        cls.state = state
    setState = classmethod(setState)

    def setTrue(cls):
        cls.state = True
    setTrue = classmethod(setTrue)

    def setFalse(cls):
        cls.state = False
    setFalse = classmethod(setFalse)

class IfTrue(Macro):
    """ Base class for all generated \\iftrues """
    def invoke(self, tex):
        type(self).ifclass.setTrue()

class IfFalse(Macro):
    """ Base class for all generated \\iffalses """
    def invoke(self, tex):
        type(self).ifclass.setFalse()

def expanddef(definition, params):
    # Walk through the definition and expand parameters
    output = []
    definition = iter(definition)
    for t in definition:
        # Expand parameters
        if t.catcode == Token.CC_PARAMETER:
            for t in definition:
                # Double '#'
                if t.catcode == Token.CC_PARAMETER:
                    output.append(t)
                else:
                    if params[int(t)] is not None:
                        output.extend(params[int(t)])
                break
        # Just append other tokens to the output
        else:
            output.append(t)
    return output

class NewCommand(Macro):
    """ Superclass for all \newcommand/\newenvironment type commands """
    nargs = 0
    opt = None
    definition = None

    def invoke(self, tex):
        if self.macroMode == Macro.MODE_END:
            return tex.context['end'+self.tagName].invoke(tex)            

        if not self.definition:
            return []

        params = [None]

        # Get optional argument, if needed
        nargs = self.nargs
        if self.opt is not None:
            nargs -= 1
            params.append(tex.readArgument('[]', default=self.opt))

        # Get mandatory arguments
        for i in range(nargs):
            params.append(tex.readArgument())

        deflog.debug2('expanding %s %s', self.definition, params)

        return expanddef(self.definition, params)

class Definition(Macro):
    """ Superclass for all \\def-type commands """
    args = None
    definition = None

    def invoke(self, tex):
        if not self.args: return self.definition

        name = macroname(self)
        argiter = iter(self.args)
        inparam = False
        params = [None]
        for a in argiter:

            # Beginning a new parameter
            if a.catcode == Token.CC_PARAMETER:

                # Adjacent parameters, just get the next token
                if inparam:
                    params.append(tex.readArgument())

                # Get the parameter number
                for a in argiter:
                    # Numbered parameter
                    if a in string.digits:
                        inparam = True

                    # Handle #{ case here
                    elif t.catcode == Token.CC_BGROUP:
                        param = []
                        for t in tex.itertokens():
                            if t.catcode == Token.CC_BGROUP:
                                tex.pushtoken(t)
                            else:
                                param.append(t)
                        inparam = False
                        params.append(param)

                    else:
                        raise ValueError, \
                              'Invalid arg string: %s' % ''.join(self.args)
                    break

            # In a parameter, so get everything up to a token that matches `a`
            elif inparam:
                param = []
                for t in tex.itertokens():
                    if t == a:
                        break
                    else:
                        param.append(t)
                inparam = False
                params.append(param)

            # Not in a parameter, just make sure the token matches
            else:
                for t in tex.itertokens():
                    if t == a:
                        break
                    else:
                        log.info('Arguments of "%s" don\'t match definition. Got "%s" but was expecting "%s" (%s).' % (name, t, a, ''.join(self.args)))
                        break

        if inparam:
            params.append(tex.readArgument())

        deflog.debug2('expanding %s %s', self.definition, params)

        return expanddef(self.definition, params)


class number(int):
    """ Class used for parameter and count values """
    def __new__(cls, v):
        if isinstance(v, Macro):
            return v.__count__()
        return int.__new__(cls, v)

    def source(self):
        return unicode(self)
    source = property(source)

class count(number): pass

class dimen(float):
    """ Class used for dimen values """

    units = ['pt','pc','in','bp','cm','mm','dd','cc','sp','ex','em']

    def __new__(cls, v):
        if isinstance(v, Macro):
            return v.__dimen__()
        elif isinstance(v, basestring) and v[-1] in string.letters:
            # Get rid of glue components
            v = list(v.split('plus').pop(0).split('minus').pop(0).strip())
            units = []
            while v and v[-1] in string.letters:
                units.insert(0, v.pop())
            v = float(''.join(v))
            units = ''.join(units) 
            if units == 'pt':
                v *= 65536
            elif units == 'pc':
                v *= 12 * 65536
            elif units == 'in':
                v *= 72.27 * 65536
            elif units == 'bp':
                v *= (72.27 * 65536) / 72
            elif units == 'cm':
                v *= (72.27 * 65536) / 2.54
            elif units == 'mm':
                v *= (72.27 * 65536) / 25.4
            elif units == 'dd':
                v *= (1238.0 * 65536) / 1157
            elif units == 'cc':
                v *= (1238.0 * 12 * 65536) / 1157
            elif units == 'sp':
                pass
            # Encode fil(ll)s by adding 2, 4, and 6 billion
            elif units == 'fil':
                if v < 0: v -= 2e9
                else: v += 2e9
            elif units == 'fill':
                if v < 0: v -= 4e9
                else: v += 4e9
            elif units == 'filll':
                if v < 0: v -= 6e9
                else: v += 6e9
            elif units == 'mu':
                pass
            # Just estimates, since I don't know the actual font size
            elif units == 'ex':
                v *= 5 * 65536
            elif units == 'em':
                v *= 11 * 65536
            else:
                raise ValueError, 'Unrecognized units: %s' % units
        return float.__new__(cls, v)

    def source(self):
        sign = 1
        if self < 0:
            sign = -1
        if abs(self) >= 6e9:
            return unicode(sign * (abs(self)-6e9)) + 'filll'
        if abs(self) >= 4e9:
            return unicode(sign * (abs(self)-4e9)) + 'fill'
        if abs(self) >= 2e9:
            return unicode(sign * (abs(self)-2e9)) + 'fil'
        return '%spt' % self.pt
    source = property(source)

    def pt(self): 
        return self / 65536
    point = pt = property(pt)

    def pc(self): 
        return self / (12 * 65536)
    pica = pc = property(pc)

    def _in(self): 
        return self / (72.27 * 65536)
    inch = _in = property(_in)

    def bp(self): 
        return self / ((72.27 * 65536) / 72)
    bigpoint = bp = property(bp)

    def cm(self): 
        return self / ((72.27 * 65536) / 2.54)
    centimeter = cm = property(cm)

    def mm(self): 
        return self / ((72.27 * 65536) / 25.4)
    millimeter = mm = property(mm)

    def dd(self): 
        return self / ((1238 * 65536) / 1157)
    didotpoint = dd = property(dd)

    def cc(self): 
        return self / ((1238 * 12 * 65536) / 1157)
    cicero = cc = property(cc)

    def sp(self): 
        return self
    scaledpoint = sp = property(sp)

    def ex(self): 
        return self / (5 * 65536)
    xheight = ex = property(ex)

    def em(self): 
        return self / (11 * 65536)
    mwidth = em = property(em)

    def fill(self):
        sign = 1
        if self < 0:
            sign = -1
        if abs(self) >= 6e9:
            return sign * (abs(self)-6e9)
        if abs(self) >= 4e9:
            return sign * (abs(self)-4e9)
        if abs(self) >= 2e9:
            return sign * (abs(self)-2e9)
        raise ValueError, 'This is not a fil(ll) dimension'
    fil = fill = filll = property(fill)

    def __repr__(self):
        return self.source

    def __str__(self):
        return self.source

class mudimen(dimen):
    """ Class used for mudimen values """
    units = ['mu']

class glue(dimen):
    """ Class used for glue values """
    def __new__(cls, g, plus=None, minus=None):
        return dimen.__new__(cls, g)
        
    def __init__(self, g, plus=None, minus=None):
        dimen.__init__(self, g)
        self.stretch = self.shrink = None
        if plus is not None:
            self.stretch = dimen(plus)
        if minus is not None:
            self.shrink = dimen(minus)

    def source(self):
        s = [dimen(self).source]
        if self.stretch is not None:
            s.append('plus')
            s.append(self.stretch.source)
        if self.shrink is not None:
            s.append('minus')
            s.append(self.shrink.source)
        return ' '.join(s)
    source = property(source)

class muglue(glue): 
    """ Class used for muglue values """
    units = ['mu']


class Parameter(Command):
    args = '= value:Number'
    value = count(0)

    enabled = True
    _enablelevel = 0
    
    def invoke(self, tex):
        if Parameter.enabled:
            # Disable invoke() in parameters nested in our arguments.
            # We don't want them to invoke, we want them to set our value.
            Parameter.enabled = False
            type(self).value = self.parse(tex)['value']
            Parameter.enabled = True

    def enable(cls):
        Parameter._enablelevel += 1
        Parameter.enabled = Parameter._enablelevel >= 0 
    enable = classmethod(enable)

    def disable(cls):
        Parameter._enablelevel -= 1
        Parameter.enabled = Parameter._enablelevel >= 0 
    disable = classmethod(disable)

    def __dimen__(self):
        return dimen(type(self).value)

    def __mudimen__(self):
        return mudimen(type(self).value)

    def __count__(self):
        return count(type(self).value)

    def __glue__(self):
        return glue(type(self).value)
    
    def __muglue__(self):
        return muglue(type(self).value)

    def the(self):
        return type(self).value.source

class Register(Parameter): pass

class Count(Register): pass

class Dimen(Register):
    args = '= value:Dimen'
    value = dimen(0)

    def setlength(self, len):
        type(self).value = dimen(len)

    def addtolength(self, len):
        type(self).value = dimen(type(self).value + len)

class MuDimen(Register):
    args = '= value:MuDimen'
    value = mudimen(0)

    def setlength(self, len):
        type(self).value = mudimen(len)

    def addtolength(self, len):
        type(self).value = mudimen(type(self).value + len)

class Glue(Register):
    args = '= value:Glue'
    value = glue(0)

    def setlength(self, len):
        type(self).value = glue(len)

    def addtolength(self, len):
        type(self).value = glue(type(self).value + len)

class MuGlue(Register):
    args = '= value:MuGlue'
    value = muglue(0)

    def setlength(self, len):
        type(self).value = muglue(len)

    def addtolength(self, len):
        type(self).value = muglue(type(self).value + len)


class Counter(object):
    """
    LaTeX counter class

    """
    counters = {}

    def __init__(self, name, resetby=None, value=0):
        self.name = name
        self.resetby = resetby
        self.value = value
        Counter.counters[name] = self

    def addtocounter(self, other):
        self.value += int(other)
        self.resetcounters()

    def stepcounter(self):
        self.value += 1
        self.resetcounters()

    def resetcounters(self):
        for counter in self.counters.values():
            if counter.resetby == self.name: 
                counter.value = 0
                counter.resetcounters()

    def __int__(self):
        return self.value

    def __float__(self):
        return self.value

    def arabic(self):
        return unicode(self.value)
    arabic = property(arabic)

    def Roman(self):
        roman = ""
        n, number = divmod(self.value, 1000)
        roman = "M"*n
        if number >= 900:
            roman = roman + "CM"
            number = number - 900
        while number >= 500:
            roman = roman + "D"
            number = number - 500
        if number >= 400:
            roman = roman + "CD"
            number = number - 400
        while number >= 100:
            roman = roman + "C"
            number = number - 100
        if number >= 90:
            roman = roman + "XC"
            number = number - 90
        while number >= 50:
            roman = roman + "L"
            number = number - 50
        if number >= 40:
            roman = roman + "XL"
            number = number - 40
        while number >= 10:
            roman = roman + "X"
            number = number - 10
        if number >= 9:
            roman = roman + "IX"
            number = number - 9
        while number >= 5:
            roman = roman + "V"
            number = number - 5
        if number >= 4:
            roman = roman + "IV"
            number = number - 4
        while number > 0:
            roman = roman + "I"
            number = number - 1
        return roman
    Roman = property(Roman)

    def roman(self):
        return self.Roman.lower()
    roman = property(roman)

    def Alph(self):
        return string.letters[self.value-1].upper()
    Alph = property(Alph)

    def alph(self):
        return self.Alph.lower()
    alph = property(alph)

    def fnsymbol(self):
        return '*' * self.value
    fnsymbol = property(fnsymbol)


class TheCounter(Command):
    """ Base class for \\thecounter commands """
    format = None

    def invoke(self, tex):

        def countervalue(m):
            """ Replace the counter values """
            parts = m.group(1).split('.')
            name = parts.pop(0)

            # If there is a reference to another \\thecounter, invoke it
            if name.startswith('the'):
                return u''.join(tex.context[name].invoke(tex))

            # Get formatted value of the requested counter
            format = 'arabic'
            if parts:
                format = parts.pop(0)

            return getattr(Counter.counters[name], format)

        format = self.format
        if self.format is None:
            format = '%%(%s.arabic)s' % self.nodeName[3:]

        return tex.texttokens(re.sub(r'%\(\s*(\w+(?:\.\w+)?)\s*\)s', 
                              countervalue, format))
