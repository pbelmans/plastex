#!/usr/bin/env python


"""
How to test this (for now):
  0) get yourself a copy of the Stacks project repository
  1) put the tags/tags file in the tags/tmp folder (which is populated after running make tags)
  2) comment out all but one reasonably sized chapter in book.tex
  3) run plastex --renderer=Gerby book.tex in tags/tmp
"""

import os, re
import plasTeX
from plasTeX.Renderers.PageTemplate import Renderer as _Renderer
from plasTeX.Renderers import Renderable

log = plasTeX.Logging.getLogger()

# TODO move this to something more reasonable like obj.ownerDocument


class GerbyRenderable(Renderable):
  def __str__(self):
    if hasattr(self, "id") and not self.id.startswith("a0"):
      log.info("%s has tag %s", self.id, self.ownerDocument.userdata["labels"][self.id])
    return Renderable.__str__(self)

  @property
  def filenameoverride(self):
    # handle tags
    if hasattr(self, "tag"):
      environment = self.nodeName
      if self.nodeName == "thmenv":
        environment = self.thmName

      return environment + "-" + self.ref + "-" + self.tag + "-" + self.id

    # handle proofs
    if self.nodeName == "proof":
      pass

    raise AttributeError


class Gerby(_Renderer):
  """ Tag-aware renderer for HTML documents """

  fileExtension = '.html'
  imageTypes = ['.png','.jpg','.jpeg','.gif']
  vectorImageTypes = ['.svg']
  renderableClass = GerbyRenderable

  def loadTags(self, document):
    """Read the tags file and construct the tags and labels dictionary"""
    with open(document.userdata["working-dir"] + "/" + document.config["gerby"]["tags"]) as f:
      content = f.readlines()

    document.userdata["tags"] = dict()
    document.userdata["labels"] = dict()

    for line in content:
      if line[0] == "#": continue

      (tag, label) = line.rstrip().split(",")
      document.userdata["tags"][tag] = label
      document.userdata["labels"][label] = tag

  def loadTemplates(self, document):
    """Load templates as in PageTemplate but also look for packages that
    want to override some templates and handles extra css and javascript."""

    try:
      import jinja2
    except ImportError:
      log.error('Jinja2 is not available, hence the HTML5 renderer cannot be used.')

    _Renderer.loadTemplates(self, document)
    rendererdata = document.rendererdata["gerby"] = dict()
    config = document.config

    rendererDir = os.path.dirname(__file__)

    srcDir = document.userdata['working-dir']
    buildDir = os.getcwd()


  def cleanup(self, document, files, postProcess=None):
    res = _Renderer.cleanup(self, document, files, postProcess=postProcess)
    return res

  def processFileContent(self, document, s):
    s = _Renderer.processFileContent(self, document, s)

    # remove empty paragraphs
    s = re.compile(r'<p>\s*</p>', re.I).sub(r'', s)

    return s

  def render(self, document):
    self.loadTags(document)

    # we decorate all DOM elements with labels that appear in the tags file
    def decorateTags(node):
      if node.nodeType == plasTeX.Macro.ELEMENT_NODE and node.id[0:2] != "a0":
        # plasTeX.Packages.hyperref parses hypertargets, but we ignore them
        if node.nodeName != "hypertarget":
          node.tag = document.userdata["labels"][node.id]

      for child in node.childNodes: decorateTags(child)

    decorateTags(document)

    _Renderer.render(self, document)

Renderer = Gerby
