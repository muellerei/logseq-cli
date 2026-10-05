"""The task rules: markers, states, reading and swapping a marker."""
import ast
import pathlib

import pytest

from logseq_cli import tasks
from tests.test_marker_lists import WORD, sites

# Generated with mldoc 1.5.7, the parser Logseq ships; not edited by hand.
# (prefix, word, separator, text, marker): marker is what mldoc reads in the
# block "- <text>", None if none.
ROWS = [
    ('', 'NOW', 'space', 'NOW x', 'NOW'),
    ('', 'NOW', 'two_spaces', 'NOW  x', 'NOW'),
    ('', 'NOW', 'tab', 'NOW\tx', None),
    ('', 'NOW', 'newline', 'NOW\nnotes', None),
    ('', 'NOW', 'space_newline', 'NOW \nnotes', 'NOW'),
    ('', 'NOW', 'end', 'NOW', 'NOW'),
    ('', 'LATER', 'space', 'LATER x', 'LATER'),
    ('', 'LATER', 'two_spaces', 'LATER  x', 'LATER'),
    ('', 'LATER', 'tab', 'LATER\tx', None),
    ('', 'LATER', 'newline', 'LATER\nnotes', None),
    ('', 'LATER', 'space_newline', 'LATER \nnotes', 'LATER'),
    ('', 'LATER', 'end', 'LATER', 'LATER'),
    ('', 'TODO', 'space', 'TODO x', 'TODO'),
    ('', 'TODO', 'two_spaces', 'TODO  x', 'TODO'),
    ('', 'TODO', 'tab', 'TODO\tx', None),
    ('', 'TODO', 'newline', 'TODO\nnotes', None),
    ('', 'TODO', 'space_newline', 'TODO \nnotes', 'TODO'),
    ('', 'TODO', 'end', 'TODO', 'TODO'),
    ('', 'DOING', 'space', 'DOING x', 'DOING'),
    ('', 'DOING', 'two_spaces', 'DOING  x', 'DOING'),
    ('', 'DOING', 'tab', 'DOING\tx', None),
    ('', 'DOING', 'newline', 'DOING\nnotes', None),
    ('', 'DOING', 'space_newline', 'DOING \nnotes', 'DOING'),
    ('', 'DOING', 'end', 'DOING', 'DOING'),
    ('', 'IN-PROGRESS', 'space', 'IN-PROGRESS x', 'IN-PROGRESS'),
    ('', 'IN-PROGRESS', 'two_spaces', 'IN-PROGRESS  x', 'IN-PROGRESS'),
    ('', 'IN-PROGRESS', 'tab', 'IN-PROGRESS\tx', None),
    ('', 'IN-PROGRESS', 'newline', 'IN-PROGRESS\nnotes', None),
    ('', 'IN-PROGRESS', 'space_newline', 'IN-PROGRESS \nnotes', 'IN-PROGRESS'),
    ('', 'IN-PROGRESS', 'end', 'IN-PROGRESS', 'IN-PROGRESS'),
    ('', 'WAIT', 'space', 'WAIT x', 'WAIT'),
    ('', 'WAIT', 'two_spaces', 'WAIT  x', 'WAIT'),
    ('', 'WAIT', 'tab', 'WAIT\tx', None),
    ('', 'WAIT', 'newline', 'WAIT\nnotes', None),
    ('', 'WAIT', 'space_newline', 'WAIT \nnotes', 'WAIT'),
    ('', 'WAIT', 'end', 'WAIT', 'WAIT'),
    ('', 'WAITING', 'space', 'WAITING x', 'WAITING'),
    ('', 'WAITING', 'two_spaces', 'WAITING  x', 'WAITING'),
    ('', 'WAITING', 'tab', 'WAITING\tx', None),
    ('', 'WAITING', 'newline', 'WAITING\nnotes', None),
    ('', 'WAITING', 'space_newline', 'WAITING \nnotes', 'WAITING'),
    ('', 'WAITING', 'end', 'WAITING', 'WAITING'),
    ('', 'STARTED', 'space', 'STARTED x', 'STARTED'),
    ('', 'STARTED', 'two_spaces', 'STARTED  x', 'STARTED'),
    ('', 'STARTED', 'tab', 'STARTED\tx', None),
    ('', 'STARTED', 'newline', 'STARTED\nnotes', None),
    ('', 'STARTED', 'space_newline', 'STARTED \nnotes', 'STARTED'),
    ('', 'STARTED', 'end', 'STARTED', 'STARTED'),
    ('', 'DONE', 'space', 'DONE x', 'DONE'),
    ('', 'DONE', 'two_spaces', 'DONE  x', 'DONE'),
    ('', 'DONE', 'tab', 'DONE\tx', None),
    ('', 'DONE', 'newline', 'DONE\nnotes', None),
    ('', 'DONE', 'space_newline', 'DONE \nnotes', 'DONE'),
    ('', 'DONE', 'end', 'DONE', 'DONE'),
    ('', 'CANCELED', 'space', 'CANCELED x', 'CANCELED'),
    ('', 'CANCELED', 'two_spaces', 'CANCELED  x', 'CANCELED'),
    ('', 'CANCELED', 'tab', 'CANCELED\tx', None),
    ('', 'CANCELED', 'newline', 'CANCELED\nnotes', None),
    ('', 'CANCELED', 'space_newline', 'CANCELED \nnotes', 'CANCELED'),
    ('', 'CANCELED', 'end', 'CANCELED', 'CANCELED'),
    ('', 'CANCELLED', 'space', 'CANCELLED x', 'CANCELLED'),
    ('', 'CANCELLED', 'two_spaces', 'CANCELLED  x', 'CANCELLED'),
    ('', 'CANCELLED', 'tab', 'CANCELLED\tx', None),
    ('', 'CANCELLED', 'newline', 'CANCELLED\nnotes', None),
    ('', 'CANCELLED', 'space_newline', 'CANCELLED \nnotes', 'CANCELLED'),
    ('', 'CANCELLED', 'end', 'CANCELLED', 'CANCELLED'),
    ('', 'todo', 'space', 'todo x', None),
    ('', 'todo', 'two_spaces', 'todo  x', None),
    ('', 'todo', 'tab', 'todo\tx', None),
    ('', 'todo', 'newline', 'todo\nnotes', None),
    ('', 'todo', 'space_newline', 'todo \nnotes', None),
    ('', 'todo', 'end', 'todo', None),
    ('', 'Todo', 'space', 'Todo x', None),
    ('', 'Todo', 'two_spaces', 'Todo  x', None),
    ('', 'Todo', 'tab', 'Todo\tx', None),
    ('', 'Todo', 'newline', 'Todo\nnotes', None),
    ('', 'Todo', 'space_newline', 'Todo \nnotes', None),
    ('', 'Todo', 'end', 'Todo', None),
    ('', 'TODOa', 'space', 'TODOa x', None),
    ('', 'TODOa', 'two_spaces', 'TODOa  x', None),
    ('', 'TODOa', 'tab', 'TODOa\tx', None),
    ('', 'TODOa', 'newline', 'TODOa\nnotes', None),
    ('', 'TODOa', 'space_newline', 'TODOa \nnotes', None),
    ('', 'TODOa', 'end', 'TODOa', None),
    ('', 'TODO:', 'space', 'TODO: x', None),
    ('', 'TODO:', 'two_spaces', 'TODO:  x', None),
    ('', 'TODO:', 'tab', 'TODO:\tx', None),
    ('', 'TODO:', 'newline', 'TODO:\nnotes', None),
    ('', 'TODO:', 'space_newline', 'TODO: \nnotes', None),
    ('', 'TODO:', 'end', 'TODO:', None),
    ('# ', 'NOW', 'space', '# NOW x', 'NOW'),
    ('# ', 'NOW', 'two_spaces', '# NOW  x', 'NOW'),
    ('# ', 'NOW', 'tab', '# NOW\tx', None),
    ('# ', 'NOW', 'newline', '# NOW\nnotes', None),
    ('# ', 'NOW', 'space_newline', '# NOW \nnotes', 'NOW'),
    ('# ', 'NOW', 'end', '# NOW', 'NOW'),
    ('# ', 'LATER', 'space', '# LATER x', 'LATER'),
    ('# ', 'LATER', 'two_spaces', '# LATER  x', 'LATER'),
    ('# ', 'LATER', 'tab', '# LATER\tx', None),
    ('# ', 'LATER', 'newline', '# LATER\nnotes', None),
    ('# ', 'LATER', 'space_newline', '# LATER \nnotes', 'LATER'),
    ('# ', 'LATER', 'end', '# LATER', 'LATER'),
    ('# ', 'TODO', 'space', '# TODO x', 'TODO'),
    ('# ', 'TODO', 'two_spaces', '# TODO  x', 'TODO'),
    ('# ', 'TODO', 'tab', '# TODO\tx', None),
    ('# ', 'TODO', 'newline', '# TODO\nnotes', None),
    ('# ', 'TODO', 'space_newline', '# TODO \nnotes', 'TODO'),
    ('# ', 'TODO', 'end', '# TODO', 'TODO'),
    ('# ', 'DOING', 'space', '# DOING x', 'DOING'),
    ('# ', 'DOING', 'two_spaces', '# DOING  x', 'DOING'),
    ('# ', 'DOING', 'tab', '# DOING\tx', None),
    ('# ', 'DOING', 'newline', '# DOING\nnotes', None),
    ('# ', 'DOING', 'space_newline', '# DOING \nnotes', 'DOING'),
    ('# ', 'DOING', 'end', '# DOING', 'DOING'),
    ('# ', 'IN-PROGRESS', 'space', '# IN-PROGRESS x', 'IN-PROGRESS'),
    ('# ', 'IN-PROGRESS', 'two_spaces', '# IN-PROGRESS  x', 'IN-PROGRESS'),
    ('# ', 'IN-PROGRESS', 'tab', '# IN-PROGRESS\tx', None),
    ('# ', 'IN-PROGRESS', 'newline', '# IN-PROGRESS\nnotes', None),
    ('# ', 'IN-PROGRESS', 'space_newline', '# IN-PROGRESS \nnotes', 'IN-PROGRESS'),
    ('# ', 'IN-PROGRESS', 'end', '# IN-PROGRESS', 'IN-PROGRESS'),
    ('# ', 'WAIT', 'space', '# WAIT x', 'WAIT'),
    ('# ', 'WAIT', 'two_spaces', '# WAIT  x', 'WAIT'),
    ('# ', 'WAIT', 'tab', '# WAIT\tx', None),
    ('# ', 'WAIT', 'newline', '# WAIT\nnotes', None),
    ('# ', 'WAIT', 'space_newline', '# WAIT \nnotes', 'WAIT'),
    ('# ', 'WAIT', 'end', '# WAIT', 'WAIT'),
    ('# ', 'WAITING', 'space', '# WAITING x', 'WAITING'),
    ('# ', 'WAITING', 'two_spaces', '# WAITING  x', 'WAITING'),
    ('# ', 'WAITING', 'tab', '# WAITING\tx', None),
    ('# ', 'WAITING', 'newline', '# WAITING\nnotes', None),
    ('# ', 'WAITING', 'space_newline', '# WAITING \nnotes', 'WAITING'),
    ('# ', 'WAITING', 'end', '# WAITING', 'WAITING'),
    ('# ', 'STARTED', 'space', '# STARTED x', 'STARTED'),
    ('# ', 'STARTED', 'two_spaces', '# STARTED  x', 'STARTED'),
    ('# ', 'STARTED', 'tab', '# STARTED\tx', None),
    ('# ', 'STARTED', 'newline', '# STARTED\nnotes', None),
    ('# ', 'STARTED', 'space_newline', '# STARTED \nnotes', 'STARTED'),
    ('# ', 'STARTED', 'end', '# STARTED', 'STARTED'),
    ('# ', 'DONE', 'space', '# DONE x', 'DONE'),
    ('# ', 'DONE', 'two_spaces', '# DONE  x', 'DONE'),
    ('# ', 'DONE', 'tab', '# DONE\tx', None),
    ('# ', 'DONE', 'newline', '# DONE\nnotes', None),
    ('# ', 'DONE', 'space_newline', '# DONE \nnotes', 'DONE'),
    ('# ', 'DONE', 'end', '# DONE', 'DONE'),
    ('# ', 'CANCELED', 'space', '# CANCELED x', 'CANCELED'),
    ('# ', 'CANCELED', 'two_spaces', '# CANCELED  x', 'CANCELED'),
    ('# ', 'CANCELED', 'tab', '# CANCELED\tx', None),
    ('# ', 'CANCELED', 'newline', '# CANCELED\nnotes', None),
    ('# ', 'CANCELED', 'space_newline', '# CANCELED \nnotes', 'CANCELED'),
    ('# ', 'CANCELED', 'end', '# CANCELED', 'CANCELED'),
    ('# ', 'CANCELLED', 'space', '# CANCELLED x', 'CANCELLED'),
    ('# ', 'CANCELLED', 'two_spaces', '# CANCELLED  x', 'CANCELLED'),
    ('# ', 'CANCELLED', 'tab', '# CANCELLED\tx', None),
    ('# ', 'CANCELLED', 'newline', '# CANCELLED\nnotes', None),
    ('# ', 'CANCELLED', 'space_newline', '# CANCELLED \nnotes', 'CANCELLED'),
    ('# ', 'CANCELLED', 'end', '# CANCELLED', 'CANCELLED'),
    ('# ', 'todo', 'space', '# todo x', None),
    ('# ', 'todo', 'two_spaces', '# todo  x', None),
    ('# ', 'todo', 'tab', '# todo\tx', None),
    ('# ', 'todo', 'newline', '# todo\nnotes', None),
    ('# ', 'todo', 'space_newline', '# todo \nnotes', None),
    ('# ', 'todo', 'end', '# todo', None),
    ('# ', 'Todo', 'space', '# Todo x', None),
    ('# ', 'Todo', 'two_spaces', '# Todo  x', None),
    ('# ', 'Todo', 'tab', '# Todo\tx', None),
    ('# ', 'Todo', 'newline', '# Todo\nnotes', None),
    ('# ', 'Todo', 'space_newline', '# Todo \nnotes', None),
    ('# ', 'Todo', 'end', '# Todo', None),
    ('# ', 'TODOa', 'space', '# TODOa x', None),
    ('# ', 'TODOa', 'two_spaces', '# TODOa  x', None),
    ('# ', 'TODOa', 'tab', '# TODOa\tx', None),
    ('# ', 'TODOa', 'newline', '# TODOa\nnotes', None),
    ('# ', 'TODOa', 'space_newline', '# TODOa \nnotes', None),
    ('# ', 'TODOa', 'end', '# TODOa', None),
    ('# ', 'TODO:', 'space', '# TODO: x', None),
    ('# ', 'TODO:', 'two_spaces', '# TODO:  x', None),
    ('# ', 'TODO:', 'tab', '# TODO:\tx', None),
    ('# ', 'TODO:', 'newline', '# TODO:\nnotes', None),
    ('# ', 'TODO:', 'space_newline', '# TODO: \nnotes', None),
    ('# ', 'TODO:', 'end', '# TODO:', None),
    ('## ', 'NOW', 'space', '## NOW x', 'NOW'),
    ('## ', 'NOW', 'two_spaces', '## NOW  x', 'NOW'),
    ('## ', 'NOW', 'tab', '## NOW\tx', None),
    ('## ', 'NOW', 'newline', '## NOW\nnotes', None),
    ('## ', 'NOW', 'space_newline', '## NOW \nnotes', 'NOW'),
    ('## ', 'NOW', 'end', '## NOW', 'NOW'),
    ('## ', 'LATER', 'space', '## LATER x', 'LATER'),
    ('## ', 'LATER', 'two_spaces', '## LATER  x', 'LATER'),
    ('## ', 'LATER', 'tab', '## LATER\tx', None),
    ('## ', 'LATER', 'newline', '## LATER\nnotes', None),
    ('## ', 'LATER', 'space_newline', '## LATER \nnotes', 'LATER'),
    ('## ', 'LATER', 'end', '## LATER', 'LATER'),
    ('## ', 'TODO', 'space', '## TODO x', 'TODO'),
    ('## ', 'TODO', 'two_spaces', '## TODO  x', 'TODO'),
    ('## ', 'TODO', 'tab', '## TODO\tx', None),
    ('## ', 'TODO', 'newline', '## TODO\nnotes', None),
    ('## ', 'TODO', 'space_newline', '## TODO \nnotes', 'TODO'),
    ('## ', 'TODO', 'end', '## TODO', 'TODO'),
    ('## ', 'DOING', 'space', '## DOING x', 'DOING'),
    ('## ', 'DOING', 'two_spaces', '## DOING  x', 'DOING'),
    ('## ', 'DOING', 'tab', '## DOING\tx', None),
    ('## ', 'DOING', 'newline', '## DOING\nnotes', None),
    ('## ', 'DOING', 'space_newline', '## DOING \nnotes', 'DOING'),
    ('## ', 'DOING', 'end', '## DOING', 'DOING'),
    ('## ', 'IN-PROGRESS', 'space', '## IN-PROGRESS x', 'IN-PROGRESS'),
    ('## ', 'IN-PROGRESS', 'two_spaces', '## IN-PROGRESS  x', 'IN-PROGRESS'),
    ('## ', 'IN-PROGRESS', 'tab', '## IN-PROGRESS\tx', None),
    ('## ', 'IN-PROGRESS', 'newline', '## IN-PROGRESS\nnotes', None),
    ('## ', 'IN-PROGRESS', 'space_newline', '## IN-PROGRESS \nnotes', 'IN-PROGRESS'),
    ('## ', 'IN-PROGRESS', 'end', '## IN-PROGRESS', 'IN-PROGRESS'),
    ('## ', 'WAIT', 'space', '## WAIT x', 'WAIT'),
    ('## ', 'WAIT', 'two_spaces', '## WAIT  x', 'WAIT'),
    ('## ', 'WAIT', 'tab', '## WAIT\tx', None),
    ('## ', 'WAIT', 'newline', '## WAIT\nnotes', None),
    ('## ', 'WAIT', 'space_newline', '## WAIT \nnotes', 'WAIT'),
    ('## ', 'WAIT', 'end', '## WAIT', 'WAIT'),
    ('## ', 'WAITING', 'space', '## WAITING x', 'WAITING'),
    ('## ', 'WAITING', 'two_spaces', '## WAITING  x', 'WAITING'),
    ('## ', 'WAITING', 'tab', '## WAITING\tx', None),
    ('## ', 'WAITING', 'newline', '## WAITING\nnotes', None),
    ('## ', 'WAITING', 'space_newline', '## WAITING \nnotes', 'WAITING'),
    ('## ', 'WAITING', 'end', '## WAITING', 'WAITING'),
    ('## ', 'STARTED', 'space', '## STARTED x', 'STARTED'),
    ('## ', 'STARTED', 'two_spaces', '## STARTED  x', 'STARTED'),
    ('## ', 'STARTED', 'tab', '## STARTED\tx', None),
    ('## ', 'STARTED', 'newline', '## STARTED\nnotes', None),
    ('## ', 'STARTED', 'space_newline', '## STARTED \nnotes', 'STARTED'),
    ('## ', 'STARTED', 'end', '## STARTED', 'STARTED'),
    ('## ', 'DONE', 'space', '## DONE x', 'DONE'),
    ('## ', 'DONE', 'two_spaces', '## DONE  x', 'DONE'),
    ('## ', 'DONE', 'tab', '## DONE\tx', None),
    ('## ', 'DONE', 'newline', '## DONE\nnotes', None),
    ('## ', 'DONE', 'space_newline', '## DONE \nnotes', 'DONE'),
    ('## ', 'DONE', 'end', '## DONE', 'DONE'),
    ('## ', 'CANCELED', 'space', '## CANCELED x', 'CANCELED'),
    ('## ', 'CANCELED', 'two_spaces', '## CANCELED  x', 'CANCELED'),
    ('## ', 'CANCELED', 'tab', '## CANCELED\tx', None),
    ('## ', 'CANCELED', 'newline', '## CANCELED\nnotes', None),
    ('## ', 'CANCELED', 'space_newline', '## CANCELED \nnotes', 'CANCELED'),
    ('## ', 'CANCELED', 'end', '## CANCELED', 'CANCELED'),
    ('## ', 'CANCELLED', 'space', '## CANCELLED x', 'CANCELLED'),
    ('## ', 'CANCELLED', 'two_spaces', '## CANCELLED  x', 'CANCELLED'),
    ('## ', 'CANCELLED', 'tab', '## CANCELLED\tx', None),
    ('## ', 'CANCELLED', 'newline', '## CANCELLED\nnotes', None),
    ('## ', 'CANCELLED', 'space_newline', '## CANCELLED \nnotes', 'CANCELLED'),
    ('## ', 'CANCELLED', 'end', '## CANCELLED', 'CANCELLED'),
    ('## ', 'todo', 'space', '## todo x', None),
    ('## ', 'todo', 'two_spaces', '## todo  x', None),
    ('## ', 'todo', 'tab', '## todo\tx', None),
    ('## ', 'todo', 'newline', '## todo\nnotes', None),
    ('## ', 'todo', 'space_newline', '## todo \nnotes', None),
    ('## ', 'todo', 'end', '## todo', None),
    ('## ', 'Todo', 'space', '## Todo x', None),
    ('## ', 'Todo', 'two_spaces', '## Todo  x', None),
    ('## ', 'Todo', 'tab', '## Todo\tx', None),
    ('## ', 'Todo', 'newline', '## Todo\nnotes', None),
    ('## ', 'Todo', 'space_newline', '## Todo \nnotes', None),
    ('## ', 'Todo', 'end', '## Todo', None),
    ('## ', 'TODOa', 'space', '## TODOa x', None),
    ('## ', 'TODOa', 'two_spaces', '## TODOa  x', None),
    ('## ', 'TODOa', 'tab', '## TODOa\tx', None),
    ('## ', 'TODOa', 'newline', '## TODOa\nnotes', None),
    ('## ', 'TODOa', 'space_newline', '## TODOa \nnotes', None),
    ('## ', 'TODOa', 'end', '## TODOa', None),
    ('## ', 'TODO:', 'space', '## TODO: x', None),
    ('## ', 'TODO:', 'two_spaces', '## TODO:  x', None),
    ('## ', 'TODO:', 'tab', '## TODO:\tx', None),
    ('## ', 'TODO:', 'newline', '## TODO:\nnotes', None),
    ('## ', 'TODO:', 'space_newline', '## TODO: \nnotes', None),
    ('## ', 'TODO:', 'end', '## TODO:', None),
    ('##', 'NOW', 'space', '##NOW x', None),
    ('##', 'NOW', 'two_spaces', '##NOW  x', None),
    ('##', 'NOW', 'tab', '##NOW\tx', None),
    ('##', 'NOW', 'newline', '##NOW\nnotes', None),
    ('##', 'NOW', 'space_newline', '##NOW \nnotes', None),
    ('##', 'NOW', 'end', '##NOW', None),
    ('##', 'LATER', 'space', '##LATER x', None),
    ('##', 'LATER', 'two_spaces', '##LATER  x', None),
    ('##', 'LATER', 'tab', '##LATER\tx', None),
    ('##', 'LATER', 'newline', '##LATER\nnotes', None),
    ('##', 'LATER', 'space_newline', '##LATER \nnotes', None),
    ('##', 'LATER', 'end', '##LATER', None),
    ('##', 'TODO', 'space', '##TODO x', None),
    ('##', 'TODO', 'two_spaces', '##TODO  x', None),
    ('##', 'TODO', 'tab', '##TODO\tx', None),
    ('##', 'TODO', 'newline', '##TODO\nnotes', None),
    ('##', 'TODO', 'space_newline', '##TODO \nnotes', None),
    ('##', 'TODO', 'end', '##TODO', None),
    ('##', 'DOING', 'space', '##DOING x', None),
    ('##', 'DOING', 'two_spaces', '##DOING  x', None),
    ('##', 'DOING', 'tab', '##DOING\tx', None),
    ('##', 'DOING', 'newline', '##DOING\nnotes', None),
    ('##', 'DOING', 'space_newline', '##DOING \nnotes', None),
    ('##', 'DOING', 'end', '##DOING', None),
    ('##', 'IN-PROGRESS', 'space', '##IN-PROGRESS x', None),
    ('##', 'IN-PROGRESS', 'two_spaces', '##IN-PROGRESS  x', None),
    ('##', 'IN-PROGRESS', 'tab', '##IN-PROGRESS\tx', None),
    ('##', 'IN-PROGRESS', 'newline', '##IN-PROGRESS\nnotes', None),
    ('##', 'IN-PROGRESS', 'space_newline', '##IN-PROGRESS \nnotes', None),
    ('##', 'IN-PROGRESS', 'end', '##IN-PROGRESS', None),
    ('##', 'WAIT', 'space', '##WAIT x', None),
    ('##', 'WAIT', 'two_spaces', '##WAIT  x', None),
    ('##', 'WAIT', 'tab', '##WAIT\tx', None),
    ('##', 'WAIT', 'newline', '##WAIT\nnotes', None),
    ('##', 'WAIT', 'space_newline', '##WAIT \nnotes', None),
    ('##', 'WAIT', 'end', '##WAIT', None),
    ('##', 'WAITING', 'space', '##WAITING x', None),
    ('##', 'WAITING', 'two_spaces', '##WAITING  x', None),
    ('##', 'WAITING', 'tab', '##WAITING\tx', None),
    ('##', 'WAITING', 'newline', '##WAITING\nnotes', None),
    ('##', 'WAITING', 'space_newline', '##WAITING \nnotes', None),
    ('##', 'WAITING', 'end', '##WAITING', None),
    ('##', 'STARTED', 'space', '##STARTED x', None),
    ('##', 'STARTED', 'two_spaces', '##STARTED  x', None),
    ('##', 'STARTED', 'tab', '##STARTED\tx', None),
    ('##', 'STARTED', 'newline', '##STARTED\nnotes', None),
    ('##', 'STARTED', 'space_newline', '##STARTED \nnotes', None),
    ('##', 'STARTED', 'end', '##STARTED', None),
    ('##', 'DONE', 'space', '##DONE x', None),
    ('##', 'DONE', 'two_spaces', '##DONE  x', None),
    ('##', 'DONE', 'tab', '##DONE\tx', None),
    ('##', 'DONE', 'newline', '##DONE\nnotes', None),
    ('##', 'DONE', 'space_newline', '##DONE \nnotes', None),
    ('##', 'DONE', 'end', '##DONE', None),
    ('##', 'CANCELED', 'space', '##CANCELED x', None),
    ('##', 'CANCELED', 'two_spaces', '##CANCELED  x', None),
    ('##', 'CANCELED', 'tab', '##CANCELED\tx', None),
    ('##', 'CANCELED', 'newline', '##CANCELED\nnotes', None),
    ('##', 'CANCELED', 'space_newline', '##CANCELED \nnotes', None),
    ('##', 'CANCELED', 'end', '##CANCELED', None),
    ('##', 'CANCELLED', 'space', '##CANCELLED x', None),
    ('##', 'CANCELLED', 'two_spaces', '##CANCELLED  x', None),
    ('##', 'CANCELLED', 'tab', '##CANCELLED\tx', None),
    ('##', 'CANCELLED', 'newline', '##CANCELLED\nnotes', None),
    ('##', 'CANCELLED', 'space_newline', '##CANCELLED \nnotes', None),
    ('##', 'CANCELLED', 'end', '##CANCELLED', None),
    ('##', 'todo', 'space', '##todo x', None),
    ('##', 'todo', 'two_spaces', '##todo  x', None),
    ('##', 'todo', 'tab', '##todo\tx', None),
    ('##', 'todo', 'newline', '##todo\nnotes', None),
    ('##', 'todo', 'space_newline', '##todo \nnotes', None),
    ('##', 'todo', 'end', '##todo', None),
    ('##', 'Todo', 'space', '##Todo x', None),
    ('##', 'Todo', 'two_spaces', '##Todo  x', None),
    ('##', 'Todo', 'tab', '##Todo\tx', None),
    ('##', 'Todo', 'newline', '##Todo\nnotes', None),
    ('##', 'Todo', 'space_newline', '##Todo \nnotes', None),
    ('##', 'Todo', 'end', '##Todo', None),
    ('##', 'TODOa', 'space', '##TODOa x', None),
    ('##', 'TODOa', 'two_spaces', '##TODOa  x', None),
    ('##', 'TODOa', 'tab', '##TODOa\tx', None),
    ('##', 'TODOa', 'newline', '##TODOa\nnotes', None),
    ('##', 'TODOa', 'space_newline', '##TODOa \nnotes', None),
    ('##', 'TODOa', 'end', '##TODOa', None),
    ('##', 'TODO:', 'space', '##TODO: x', None),
    ('##', 'TODO:', 'two_spaces', '##TODO:  x', None),
    ('##', 'TODO:', 'tab', '##TODO:\tx', None),
    ('##', 'TODO:', 'newline', '##TODO:\nnotes', None),
    ('##', 'TODO:', 'space_newline', '##TODO: \nnotes', None),
    ('##', 'TODO:', 'end', '##TODO:', None),
    ('#  ', 'NOW', 'space', '#  NOW x', 'NOW'),
    ('#  ', 'NOW', 'two_spaces', '#  NOW  x', 'NOW'),
    ('#  ', 'NOW', 'tab', '#  NOW\tx', None),
    ('#  ', 'NOW', 'newline', '#  NOW\nnotes', None),
    ('#  ', 'NOW', 'space_newline', '#  NOW \nnotes', 'NOW'),
    ('#  ', 'NOW', 'end', '#  NOW', 'NOW'),
    ('#  ', 'LATER', 'space', '#  LATER x', 'LATER'),
    ('#  ', 'LATER', 'two_spaces', '#  LATER  x', 'LATER'),
    ('#  ', 'LATER', 'tab', '#  LATER\tx', None),
    ('#  ', 'LATER', 'newline', '#  LATER\nnotes', None),
    ('#  ', 'LATER', 'space_newline', '#  LATER \nnotes', 'LATER'),
    ('#  ', 'LATER', 'end', '#  LATER', 'LATER'),
    ('#  ', 'TODO', 'space', '#  TODO x', 'TODO'),
    ('#  ', 'TODO', 'two_spaces', '#  TODO  x', 'TODO'),
    ('#  ', 'TODO', 'tab', '#  TODO\tx', None),
    ('#  ', 'TODO', 'newline', '#  TODO\nnotes', None),
    ('#  ', 'TODO', 'space_newline', '#  TODO \nnotes', 'TODO'),
    ('#  ', 'TODO', 'end', '#  TODO', 'TODO'),
    ('#  ', 'DOING', 'space', '#  DOING x', 'DOING'),
    ('#  ', 'DOING', 'two_spaces', '#  DOING  x', 'DOING'),
    ('#  ', 'DOING', 'tab', '#  DOING\tx', None),
    ('#  ', 'DOING', 'newline', '#  DOING\nnotes', None),
    ('#  ', 'DOING', 'space_newline', '#  DOING \nnotes', 'DOING'),
    ('#  ', 'DOING', 'end', '#  DOING', 'DOING'),
    ('#  ', 'IN-PROGRESS', 'space', '#  IN-PROGRESS x', 'IN-PROGRESS'),
    ('#  ', 'IN-PROGRESS', 'two_spaces', '#  IN-PROGRESS  x', 'IN-PROGRESS'),
    ('#  ', 'IN-PROGRESS', 'tab', '#  IN-PROGRESS\tx', None),
    ('#  ', 'IN-PROGRESS', 'newline', '#  IN-PROGRESS\nnotes', None),
    ('#  ', 'IN-PROGRESS', 'space_newline', '#  IN-PROGRESS \nnotes', 'IN-PROGRESS'),
    ('#  ', 'IN-PROGRESS', 'end', '#  IN-PROGRESS', 'IN-PROGRESS'),
    ('#  ', 'WAIT', 'space', '#  WAIT x', 'WAIT'),
    ('#  ', 'WAIT', 'two_spaces', '#  WAIT  x', 'WAIT'),
    ('#  ', 'WAIT', 'tab', '#  WAIT\tx', None),
    ('#  ', 'WAIT', 'newline', '#  WAIT\nnotes', None),
    ('#  ', 'WAIT', 'space_newline', '#  WAIT \nnotes', 'WAIT'),
    ('#  ', 'WAIT', 'end', '#  WAIT', 'WAIT'),
    ('#  ', 'WAITING', 'space', '#  WAITING x', 'WAITING'),
    ('#  ', 'WAITING', 'two_spaces', '#  WAITING  x', 'WAITING'),
    ('#  ', 'WAITING', 'tab', '#  WAITING\tx', None),
    ('#  ', 'WAITING', 'newline', '#  WAITING\nnotes', None),
    ('#  ', 'WAITING', 'space_newline', '#  WAITING \nnotes', 'WAITING'),
    ('#  ', 'WAITING', 'end', '#  WAITING', 'WAITING'),
    ('#  ', 'STARTED', 'space', '#  STARTED x', 'STARTED'),
    ('#  ', 'STARTED', 'two_spaces', '#  STARTED  x', 'STARTED'),
    ('#  ', 'STARTED', 'tab', '#  STARTED\tx', None),
    ('#  ', 'STARTED', 'newline', '#  STARTED\nnotes', None),
    ('#  ', 'STARTED', 'space_newline', '#  STARTED \nnotes', 'STARTED'),
    ('#  ', 'STARTED', 'end', '#  STARTED', 'STARTED'),
    ('#  ', 'DONE', 'space', '#  DONE x', 'DONE'),
    ('#  ', 'DONE', 'two_spaces', '#  DONE  x', 'DONE'),
    ('#  ', 'DONE', 'tab', '#  DONE\tx', None),
    ('#  ', 'DONE', 'newline', '#  DONE\nnotes', None),
    ('#  ', 'DONE', 'space_newline', '#  DONE \nnotes', 'DONE'),
    ('#  ', 'DONE', 'end', '#  DONE', 'DONE'),
    ('#  ', 'CANCELED', 'space', '#  CANCELED x', 'CANCELED'),
    ('#  ', 'CANCELED', 'two_spaces', '#  CANCELED  x', 'CANCELED'),
    ('#  ', 'CANCELED', 'tab', '#  CANCELED\tx', None),
    ('#  ', 'CANCELED', 'newline', '#  CANCELED\nnotes', None),
    ('#  ', 'CANCELED', 'space_newline', '#  CANCELED \nnotes', 'CANCELED'),
    ('#  ', 'CANCELED', 'end', '#  CANCELED', 'CANCELED'),
    ('#  ', 'CANCELLED', 'space', '#  CANCELLED x', 'CANCELLED'),
    ('#  ', 'CANCELLED', 'two_spaces', '#  CANCELLED  x', 'CANCELLED'),
    ('#  ', 'CANCELLED', 'tab', '#  CANCELLED\tx', None),
    ('#  ', 'CANCELLED', 'newline', '#  CANCELLED\nnotes', None),
    ('#  ', 'CANCELLED', 'space_newline', '#  CANCELLED \nnotes', 'CANCELLED'),
    ('#  ', 'CANCELLED', 'end', '#  CANCELLED', 'CANCELLED'),
    ('#  ', 'todo', 'space', '#  todo x', None),
    ('#  ', 'todo', 'two_spaces', '#  todo  x', None),
    ('#  ', 'todo', 'tab', '#  todo\tx', None),
    ('#  ', 'todo', 'newline', '#  todo\nnotes', None),
    ('#  ', 'todo', 'space_newline', '#  todo \nnotes', None),
    ('#  ', 'todo', 'end', '#  todo', None),
    ('#  ', 'Todo', 'space', '#  Todo x', None),
    ('#  ', 'Todo', 'two_spaces', '#  Todo  x', None),
    ('#  ', 'Todo', 'tab', '#  Todo\tx', None),
    ('#  ', 'Todo', 'newline', '#  Todo\nnotes', None),
    ('#  ', 'Todo', 'space_newline', '#  Todo \nnotes', None),
    ('#  ', 'Todo', 'end', '#  Todo', None),
    ('#  ', 'TODOa', 'space', '#  TODOa x', None),
    ('#  ', 'TODOa', 'two_spaces', '#  TODOa  x', None),
    ('#  ', 'TODOa', 'tab', '#  TODOa\tx', None),
    ('#  ', 'TODOa', 'newline', '#  TODOa\nnotes', None),
    ('#  ', 'TODOa', 'space_newline', '#  TODOa \nnotes', None),
    ('#  ', 'TODOa', 'end', '#  TODOa', None),
    ('#  ', 'TODO:', 'space', '#  TODO: x', None),
    ('#  ', 'TODO:', 'two_spaces', '#  TODO:  x', None),
    ('#  ', 'TODO:', 'tab', '#  TODO:\tx', None),
    ('#  ', 'TODO:', 'newline', '#  TODO:\nnotes', None),
    ('#  ', 'TODO:', 'space_newline', '#  TODO: \nnotes', None),
    ('#  ', 'TODO:', 'end', '#  TODO:', None),
    ('##\t', 'NOW', 'space', '##\tNOW x', 'NOW'),
    ('##\t', 'NOW', 'two_spaces', '##\tNOW  x', 'NOW'),
    ('##\t', 'NOW', 'tab', '##\tNOW\tx', None),
    ('##\t', 'NOW', 'newline', '##\tNOW\nnotes', None),
    ('##\t', 'NOW', 'space_newline', '##\tNOW \nnotes', 'NOW'),
    ('##\t', 'NOW', 'end', '##\tNOW', 'NOW'),
    ('##\t', 'LATER', 'space', '##\tLATER x', 'LATER'),
    ('##\t', 'LATER', 'two_spaces', '##\tLATER  x', 'LATER'),
    ('##\t', 'LATER', 'tab', '##\tLATER\tx', None),
    ('##\t', 'LATER', 'newline', '##\tLATER\nnotes', None),
    ('##\t', 'LATER', 'space_newline', '##\tLATER \nnotes', 'LATER'),
    ('##\t', 'LATER', 'end', '##\tLATER', 'LATER'),
    ('##\t', 'TODO', 'space', '##\tTODO x', 'TODO'),
    ('##\t', 'TODO', 'two_spaces', '##\tTODO  x', 'TODO'),
    ('##\t', 'TODO', 'tab', '##\tTODO\tx', None),
    ('##\t', 'TODO', 'newline', '##\tTODO\nnotes', None),
    ('##\t', 'TODO', 'space_newline', '##\tTODO \nnotes', 'TODO'),
    ('##\t', 'TODO', 'end', '##\tTODO', 'TODO'),
    ('##\t', 'DOING', 'space', '##\tDOING x', 'DOING'),
    ('##\t', 'DOING', 'two_spaces', '##\tDOING  x', 'DOING'),
    ('##\t', 'DOING', 'tab', '##\tDOING\tx', None),
    ('##\t', 'DOING', 'newline', '##\tDOING\nnotes', None),
    ('##\t', 'DOING', 'space_newline', '##\tDOING \nnotes', 'DOING'),
    ('##\t', 'DOING', 'end', '##\tDOING', 'DOING'),
    ('##\t', 'IN-PROGRESS', 'space', '##\tIN-PROGRESS x', 'IN-PROGRESS'),
    ('##\t', 'IN-PROGRESS', 'two_spaces', '##\tIN-PROGRESS  x', 'IN-PROGRESS'),
    ('##\t', 'IN-PROGRESS', 'tab', '##\tIN-PROGRESS\tx', None),
    ('##\t', 'IN-PROGRESS', 'newline', '##\tIN-PROGRESS\nnotes', None),
    ('##\t', 'IN-PROGRESS', 'space_newline', '##\tIN-PROGRESS \nnotes', 'IN-PROGRESS'),
    ('##\t', 'IN-PROGRESS', 'end', '##\tIN-PROGRESS', 'IN-PROGRESS'),
    ('##\t', 'WAIT', 'space', '##\tWAIT x', 'WAIT'),
    ('##\t', 'WAIT', 'two_spaces', '##\tWAIT  x', 'WAIT'),
    ('##\t', 'WAIT', 'tab', '##\tWAIT\tx', None),
    ('##\t', 'WAIT', 'newline', '##\tWAIT\nnotes', None),
    ('##\t', 'WAIT', 'space_newline', '##\tWAIT \nnotes', 'WAIT'),
    ('##\t', 'WAIT', 'end', '##\tWAIT', 'WAIT'),
    ('##\t', 'WAITING', 'space', '##\tWAITING x', 'WAITING'),
    ('##\t', 'WAITING', 'two_spaces', '##\tWAITING  x', 'WAITING'),
    ('##\t', 'WAITING', 'tab', '##\tWAITING\tx', None),
    ('##\t', 'WAITING', 'newline', '##\tWAITING\nnotes', None),
    ('##\t', 'WAITING', 'space_newline', '##\tWAITING \nnotes', 'WAITING'),
    ('##\t', 'WAITING', 'end', '##\tWAITING', 'WAITING'),
    ('##\t', 'STARTED', 'space', '##\tSTARTED x', 'STARTED'),
    ('##\t', 'STARTED', 'two_spaces', '##\tSTARTED  x', 'STARTED'),
    ('##\t', 'STARTED', 'tab', '##\tSTARTED\tx', None),
    ('##\t', 'STARTED', 'newline', '##\tSTARTED\nnotes', None),
    ('##\t', 'STARTED', 'space_newline', '##\tSTARTED \nnotes', 'STARTED'),
    ('##\t', 'STARTED', 'end', '##\tSTARTED', 'STARTED'),
    ('##\t', 'DONE', 'space', '##\tDONE x', 'DONE'),
    ('##\t', 'DONE', 'two_spaces', '##\tDONE  x', 'DONE'),
    ('##\t', 'DONE', 'tab', '##\tDONE\tx', None),
    ('##\t', 'DONE', 'newline', '##\tDONE\nnotes', None),
    ('##\t', 'DONE', 'space_newline', '##\tDONE \nnotes', 'DONE'),
    ('##\t', 'DONE', 'end', '##\tDONE', 'DONE'),
    ('##\t', 'CANCELED', 'space', '##\tCANCELED x', 'CANCELED'),
    ('##\t', 'CANCELED', 'two_spaces', '##\tCANCELED  x', 'CANCELED'),
    ('##\t', 'CANCELED', 'tab', '##\tCANCELED\tx', None),
    ('##\t', 'CANCELED', 'newline', '##\tCANCELED\nnotes', None),
    ('##\t', 'CANCELED', 'space_newline', '##\tCANCELED \nnotes', 'CANCELED'),
    ('##\t', 'CANCELED', 'end', '##\tCANCELED', 'CANCELED'),
    ('##\t', 'CANCELLED', 'space', '##\tCANCELLED x', 'CANCELLED'),
    ('##\t', 'CANCELLED', 'two_spaces', '##\tCANCELLED  x', 'CANCELLED'),
    ('##\t', 'CANCELLED', 'tab', '##\tCANCELLED\tx', None),
    ('##\t', 'CANCELLED', 'newline', '##\tCANCELLED\nnotes', None),
    ('##\t', 'CANCELLED', 'space_newline', '##\tCANCELLED \nnotes', 'CANCELLED'),
    ('##\t', 'CANCELLED', 'end', '##\tCANCELLED', 'CANCELLED'),
    ('##\t', 'todo', 'space', '##\ttodo x', None),
    ('##\t', 'todo', 'two_spaces', '##\ttodo  x', None),
    ('##\t', 'todo', 'tab', '##\ttodo\tx', None),
    ('##\t', 'todo', 'newline', '##\ttodo\nnotes', None),
    ('##\t', 'todo', 'space_newline', '##\ttodo \nnotes', None),
    ('##\t', 'todo', 'end', '##\ttodo', None),
    ('##\t', 'Todo', 'space', '##\tTodo x', None),
    ('##\t', 'Todo', 'two_spaces', '##\tTodo  x', None),
    ('##\t', 'Todo', 'tab', '##\tTodo\tx', None),
    ('##\t', 'Todo', 'newline', '##\tTodo\nnotes', None),
    ('##\t', 'Todo', 'space_newline', '##\tTodo \nnotes', None),
    ('##\t', 'Todo', 'end', '##\tTodo', None),
    ('##\t', 'TODOa', 'space', '##\tTODOa x', None),
    ('##\t', 'TODOa', 'two_spaces', '##\tTODOa  x', None),
    ('##\t', 'TODOa', 'tab', '##\tTODOa\tx', None),
    ('##\t', 'TODOa', 'newline', '##\tTODOa\nnotes', None),
    ('##\t', 'TODOa', 'space_newline', '##\tTODOa \nnotes', None),
    ('##\t', 'TODOa', 'end', '##\tTODOa', None),
    ('##\t', 'TODO:', 'space', '##\tTODO: x', None),
    ('##\t', 'TODO:', 'two_spaces', '##\tTODO:  x', None),
    ('##\t', 'TODO:', 'tab', '##\tTODO:\tx', None),
    ('##\t', 'TODO:', 'newline', '##\tTODO:\nnotes', None),
    ('##\t', 'TODO:', 'space_newline', '##\tTODO: \nnotes', None),
    ('##\t', 'TODO:', 'end', '##\tTODO:', None),
    ('# \t', 'NOW', 'space', '# \tNOW x', 'NOW'),
    ('# \t', 'NOW', 'two_spaces', '# \tNOW  x', 'NOW'),
    ('# \t', 'NOW', 'tab', '# \tNOW\tx', None),
    ('# \t', 'NOW', 'newline', '# \tNOW\nnotes', None),
    ('# \t', 'NOW', 'space_newline', '# \tNOW \nnotes', 'NOW'),
    ('# \t', 'NOW', 'end', '# \tNOW', 'NOW'),
    ('# \t', 'LATER', 'space', '# \tLATER x', 'LATER'),
    ('# \t', 'LATER', 'two_spaces', '# \tLATER  x', 'LATER'),
    ('# \t', 'LATER', 'tab', '# \tLATER\tx', None),
    ('# \t', 'LATER', 'newline', '# \tLATER\nnotes', None),
    ('# \t', 'LATER', 'space_newline', '# \tLATER \nnotes', 'LATER'),
    ('# \t', 'LATER', 'end', '# \tLATER', 'LATER'),
    ('# \t', 'TODO', 'space', '# \tTODO x', 'TODO'),
    ('# \t', 'TODO', 'two_spaces', '# \tTODO  x', 'TODO'),
    ('# \t', 'TODO', 'tab', '# \tTODO\tx', None),
    ('# \t', 'TODO', 'newline', '# \tTODO\nnotes', None),
    ('# \t', 'TODO', 'space_newline', '# \tTODO \nnotes', 'TODO'),
    ('# \t', 'TODO', 'end', '# \tTODO', 'TODO'),
    ('# \t', 'DOING', 'space', '# \tDOING x', 'DOING'),
    ('# \t', 'DOING', 'two_spaces', '# \tDOING  x', 'DOING'),
    ('# \t', 'DOING', 'tab', '# \tDOING\tx', None),
    ('# \t', 'DOING', 'newline', '# \tDOING\nnotes', None),
    ('# \t', 'DOING', 'space_newline', '# \tDOING \nnotes', 'DOING'),
    ('# \t', 'DOING', 'end', '# \tDOING', 'DOING'),
    ('# \t', 'IN-PROGRESS', 'space', '# \tIN-PROGRESS x', 'IN-PROGRESS'),
    ('# \t', 'IN-PROGRESS', 'two_spaces', '# \tIN-PROGRESS  x', 'IN-PROGRESS'),
    ('# \t', 'IN-PROGRESS', 'tab', '# \tIN-PROGRESS\tx', None),
    ('# \t', 'IN-PROGRESS', 'newline', '# \tIN-PROGRESS\nnotes', None),
    ('# \t', 'IN-PROGRESS', 'space_newline', '# \tIN-PROGRESS \nnotes', 'IN-PROGRESS'),
    ('# \t', 'IN-PROGRESS', 'end', '# \tIN-PROGRESS', 'IN-PROGRESS'),
    ('# \t', 'WAIT', 'space', '# \tWAIT x', 'WAIT'),
    ('# \t', 'WAIT', 'two_spaces', '# \tWAIT  x', 'WAIT'),
    ('# \t', 'WAIT', 'tab', '# \tWAIT\tx', None),
    ('# \t', 'WAIT', 'newline', '# \tWAIT\nnotes', None),
    ('# \t', 'WAIT', 'space_newline', '# \tWAIT \nnotes', 'WAIT'),
    ('# \t', 'WAIT', 'end', '# \tWAIT', 'WAIT'),
    ('# \t', 'WAITING', 'space', '# \tWAITING x', 'WAITING'),
    ('# \t', 'WAITING', 'two_spaces', '# \tWAITING  x', 'WAITING'),
    ('# \t', 'WAITING', 'tab', '# \tWAITING\tx', None),
    ('# \t', 'WAITING', 'newline', '# \tWAITING\nnotes', None),
    ('# \t', 'WAITING', 'space_newline', '# \tWAITING \nnotes', 'WAITING'),
    ('# \t', 'WAITING', 'end', '# \tWAITING', 'WAITING'),
    ('# \t', 'STARTED', 'space', '# \tSTARTED x', 'STARTED'),
    ('# \t', 'STARTED', 'two_spaces', '# \tSTARTED  x', 'STARTED'),
    ('# \t', 'STARTED', 'tab', '# \tSTARTED\tx', None),
    ('# \t', 'STARTED', 'newline', '# \tSTARTED\nnotes', None),
    ('# \t', 'STARTED', 'space_newline', '# \tSTARTED \nnotes', 'STARTED'),
    ('# \t', 'STARTED', 'end', '# \tSTARTED', 'STARTED'),
    ('# \t', 'DONE', 'space', '# \tDONE x', 'DONE'),
    ('# \t', 'DONE', 'two_spaces', '# \tDONE  x', 'DONE'),
    ('# \t', 'DONE', 'tab', '# \tDONE\tx', None),
    ('# \t', 'DONE', 'newline', '# \tDONE\nnotes', None),
    ('# \t', 'DONE', 'space_newline', '# \tDONE \nnotes', 'DONE'),
    ('# \t', 'DONE', 'end', '# \tDONE', 'DONE'),
    ('# \t', 'CANCELED', 'space', '# \tCANCELED x', 'CANCELED'),
    ('# \t', 'CANCELED', 'two_spaces', '# \tCANCELED  x', 'CANCELED'),
    ('# \t', 'CANCELED', 'tab', '# \tCANCELED\tx', None),
    ('# \t', 'CANCELED', 'newline', '# \tCANCELED\nnotes', None),
    ('# \t', 'CANCELED', 'space_newline', '# \tCANCELED \nnotes', 'CANCELED'),
    ('# \t', 'CANCELED', 'end', '# \tCANCELED', 'CANCELED'),
    ('# \t', 'CANCELLED', 'space', '# \tCANCELLED x', 'CANCELLED'),
    ('# \t', 'CANCELLED', 'two_spaces', '# \tCANCELLED  x', 'CANCELLED'),
    ('# \t', 'CANCELLED', 'tab', '# \tCANCELLED\tx', None),
    ('# \t', 'CANCELLED', 'newline', '# \tCANCELLED\nnotes', None),
    ('# \t', 'CANCELLED', 'space_newline', '# \tCANCELLED \nnotes', 'CANCELLED'),
    ('# \t', 'CANCELLED', 'end', '# \tCANCELLED', 'CANCELLED'),
    ('# \t', 'todo', 'space', '# \ttodo x', None),
    ('# \t', 'todo', 'two_spaces', '# \ttodo  x', None),
    ('# \t', 'todo', 'tab', '# \ttodo\tx', None),
    ('# \t', 'todo', 'newline', '# \ttodo\nnotes', None),
    ('# \t', 'todo', 'space_newline', '# \ttodo \nnotes', None),
    ('# \t', 'todo', 'end', '# \ttodo', None),
    ('# \t', 'Todo', 'space', '# \tTodo x', None),
    ('# \t', 'Todo', 'two_spaces', '# \tTodo  x', None),
    ('# \t', 'Todo', 'tab', '# \tTodo\tx', None),
    ('# \t', 'Todo', 'newline', '# \tTodo\nnotes', None),
    ('# \t', 'Todo', 'space_newline', '# \tTodo \nnotes', None),
    ('# \t', 'Todo', 'end', '# \tTodo', None),
    ('# \t', 'TODOa', 'space', '# \tTODOa x', None),
    ('# \t', 'TODOa', 'two_spaces', '# \tTODOa  x', None),
    ('# \t', 'TODOa', 'tab', '# \tTODOa\tx', None),
    ('# \t', 'TODOa', 'newline', '# \tTODOa\nnotes', None),
    ('# \t', 'TODOa', 'space_newline', '# \tTODOa \nnotes', None),
    ('# \t', 'TODOa', 'end', '# \tTODOa', None),
    ('# \t', 'TODO:', 'space', '# \tTODO: x', None),
    ('# \t', 'TODO:', 'two_spaces', '# \tTODO:  x', None),
    ('# \t', 'TODO:', 'tab', '# \tTODO:\tx', None),
    ('# \t', 'TODO:', 'newline', '# \tTODO:\nnotes', None),
    ('# \t', 'TODO:', 'space_newline', '# \tTODO: \nnotes', None),
    ('# \t', 'TODO:', 'end', '# \tTODO:', None),
]

# mldoc reads a marker after a tab behind the hashes; marker_of deliberately
# does not (it asks for spaces, because with_marker writes on what it reads).
TAB_PREFIXES = ("##\t", "# \t")
MARKER_PREFIXES = ("", "# ", "## ", "#  ")

OPEN = ["DOING", "NOW", "IN-PROGRESS", "STARTED", "TODO", "LATER", "WAIT", "WAITING"]


class TestOrderAndStates:
    """mldoc 1.5.7 reads these eleven words as :block/marker (measured on a
    page, Logseq 0.10.15, STARTED included, which Logseq's own marker-pattern
    does not name). block-checkbox (components/block.cljs, 0.10.15) draws an
    empty box for NOW LATER DOING IN-PROGRESS TODO WAIT WAITING, a ticked one
    for DONE, none for CANCELED/CANCELLED and none for STARTED either; that
    STARTED is open here is this module's decision, not a drawing."""

    def test_order(self):
        assert tasks.ORDER == ("DOING", "NOW", "IN-PROGRESS", "STARTED", "TODO", "LATER",
                               "WAIT", "WAITING", "DONE", "CANCELED", "CANCELLED")

    def test_states(self):
        assert tasks.STATE == {
            "DOING": "open", "NOW": "open", "IN-PROGRESS": "open", "STARTED": "open",
            "TODO": "open", "LATER": "open", "WAIT": "open", "WAITING": "open",
            "DONE": "done", "CANCELED": "cancelled", "CANCELLED": "cancelled"}

    def test_every_marker_has_a_state(self):
        assert set(tasks.STATE) == set(tasks.ORDER)
        assert len(tasks.ORDER) == 11

    def test_the_frontend_offers_every_marker_but_started(self):
        assert tasks.FRONTEND_MARKERS == tuple(m for m in tasks.ORDER if m != "STARTED")
        assert "STARTED" not in tasks.FRONTEND_MARKERS


class TestStates:
    def test_the_three_states_in_the_order_of_the_markers(self):
        assert tasks.STATES == ("open", "done", "cancelled")

    def test_every_state_of_a_marker_is_listed(self):
        assert set(tasks.STATE.values()) == set(tasks.STATES)


class TestMarkersIn:
    def test_open(self):
        assert tasks.markers_in(["open"]) == OPEN

    def test_done(self):
        assert tasks.markers_in(["done"]) == ["DONE"]

    def test_cancelled(self):
        assert tasks.markers_in(["cancelled"]) == ["CANCELED", "CANCELLED"]

    def test_the_order_does_not_depend_on_the_order_of_the_states(self):
        assert tasks.markers_in(["done", "open"]) == tasks.markers_in(["open", "done"]) \
            == OPEN + ["DONE"]

    def test_no_state_no_markers(self):
        assert tasks.markers_in([]) == []

    def test_an_unknown_state_raises(self):
        with pytest.raises(ValueError):
            tasks.markers_in(["finished"])


class TestMarkerClause:
    def test_two_markers(self):
        assert tasks.marker_clause("?m", ["TODO", "DOING"]) == '[(contains? #{"TODO" "DOING"} ?m)]'

    def test_every_marker_in_the_order_given(self):
        clause = tasks.marker_clause("?m", tasks.ORDER)
        assert clause == "[(contains? #{" + " ".join(f'"{m}"' for m in tasks.ORDER) + "} ?m)]"

    def test_another_variable(self):
        assert tasks.marker_clause("?x", ["DONE"]) == '[(contains? #{"DONE"} ?x)]'

    @pytest.mark.parametrize("var", ["m", "?m ?n", "", "?"])
    def test_a_bad_variable_raises(self, var):
        with pytest.raises(ValueError):
            tasks.marker_clause(var, ["TODO"])

    @pytest.mark.parametrize("marker", ["FOO", "todo"])
    def test_a_marker_outside_the_order_raises(self, marker):
        with pytest.raises(ValueError):
            tasks.marker_clause("?m", [marker])

    def test_an_empty_list_raises(self):
        with pytest.raises(ValueError):
            tasks.marker_clause("?m", [])


MARKER_OF = [
    ("## TODO a", "TODO"), ("TODO", "TODO"), ("TODO  a", "TODO"), ("# DONE x", "DONE"),
    ("IN-PROGRESS a", "IN-PROGRESS"), ("WAITING a", "WAITING"), ("WAIT a", "WAIT"),
    ("TODO \nnotes", "TODO"),
]
NO_MARKER = [
    "todo a", "Todo a", "TODO: a", "TODOa", "TODO\na", "TODO\ta", "##TODO a", "",
    "a TODO", "a note\nTODO is only a word here", "[ ] a", "[x] a",
    "Now that works", "Waiting for x",
    # mldoc reads these as tasks, marker_of deliberately does not
    "##\tTODO x", "# \tTODO x", "#\tTODO x", "##\t TODO x",
    # mldoc 1.5.7 reads no marker behind a list character (a block holding
    # "- TODO x" is a heading without a marker); the old regex counted "- - TODO"
    "- TODO a", "- DONE a", "* TODO a",
]


class TestMarkerOf:
    @pytest.mark.parametrize("text,marker", MARKER_OF)
    def test_a_marker(self, text, marker):
        assert tasks.marker_of(text) == marker

    @pytest.mark.parametrize("text", NO_MARKER)
    def test_no_marker(self, text):
        assert tasks.marker_of(text) is None

    def test_it_reads_like_mldoc_for_every_row(self):
        skipped = checked = 0
        for prefix, word, sep, text, marker in ROWS:
            if text.startswith(TAB_PREFIXES):
                assert tasks.marker_of(text) is None, text
                skipped += 1
            else:
                assert tasks.marker_of(text) == marker, text
                checked += 1
        assert skipped and checked


class TestMarkerBeforeNewline:
    @pytest.mark.parametrize("text,marker", [
        ("TODO\nnotes", "TODO"), ("DONE\nnotes", "DONE"), ("## TODO\nnotes", "TODO")])
    def test_a_marker(self, text, marker):
        assert tasks.marker_before_newline(text) == marker

    @pytest.mark.parametrize("text", [
        "TODO \nnotes", "todo\nnotes", "TODO:\nx", "TODO x", "TODO", "##TODO\nnotes"])
    def test_none(self, text):
        assert tasks.marker_before_newline(text) is None

    def test_it_is_what_mldoc_does_not_read_for_every_newline_row(self):
        seen = 0
        for prefix, word, sep, text, marker in ROWS:
            if sep != "newline":
                continue
            expected = word if (marker is None and prefix in MARKER_PREFIXES
                                and word in tasks.ORDER) else None
            assert tasks.marker_before_newline(text) == expected, text
            seen += expected is not None
        assert seen


class TestWithMarker:
    @pytest.mark.parametrize("text,new,expected", [
        ("## TODO a", "DONE", "## DONE a"),
        ("TODO  a", "DONE", "DONE a"),
        ("TODO \tx", "DONE", "DONE x"),
        ("TODO", "DONE", "DONE"),
        ("TODO ", "DONE", "DONE"),
        ("DONE", "DONE", "DONE"),
        ("TODO \nnotes", "DONE", "DONE \nnotes"),
        ("TODO a\nsecond TODO line", "DONE", "DONE a\nsecond TODO line"),
        ("#  TODO a", "DONE", "#  DONE a"),
        ("WAITING x", "DONE", "DONE x"),
        ("WAIT x", "DONE", "DONE x"),
        ("STARTED x", "TODO", "TODO x"),
        ("IN-PROGRESS x", "DONE", "DONE x"),
        ("CANCELLED x", "TODO", "TODO x"),
        ("## WAITING x", "DONE", "## DONE x"),
    ])
    def test_swap(self, text, new, expected):
        assert tasks.with_marker(text, new) == expected

    @pytest.mark.parametrize("text", ["todo a", "TODO\nnotes", "plain"])
    def test_a_text_without_a_marker_raises(self, text):
        with pytest.raises(ValueError):
            tasks.with_marker(text, "DONE")

    def test_an_unknown_marker_raises(self):
        with pytest.raises(ValueError):
            tasks.with_marker("TODO a", "FOO")


class TestStartsWithBox:
    @pytest.mark.parametrize("text", [
        "[ ] a", "[x] a", "[X] a", "[ ]a", "  [ ] a", "[ ] a\nsecond line"])
    def test_a_box(self, text):
        assert tasks.starts_with_box(text)

    @pytest.mark.parametrize("text", [
        "## [ ] x", "* [ ] a", "- [ ] a", "1. [ ] a", "TODO [ ] x", "a [ ] b", "a\n[ ] b",
        "[] a", "[  ] a", "[y] a", "",
        "tags = [ ] means an empty list", "see [ ](https://example.com)",
        "a sentence about [ ] brackets"])
    def test_no_box(self, text):
        assert not tasks.starts_with_box(text)


LOG = "CLOCK: [2026-09-29 Tue 10:00:00]--[2026-09-29 Tue 10:05:00] =>  00:05:00"


class TestTaskTextKeepsWhatItAlwaysDid:
    """Cases that read the same under the old inline code and the shared rule."""

    @pytest.mark.parametrize("content,marker,expected", [
        ("TODO", "TODO", ""),
        ("TODO x", "TODO", "x"),
        ("DONE x", "DONE", "x"),
        ("TODO x\nprio:: high", "TODO", "x"),
        ("TODO x\n  owner:: someone", "TODO", "x"),
        ("TODO x\nSCHEDULED: <2026-09-25 Fri>", "TODO", "x"),
        ("TODO x\nDEADLINE: <2026-09-25 Fri>", "TODO", "x"),
        (f"TODO x\n:LOGBOOK:\n{LOG}\n:END:\nafter", "TODO", "x\nafter"),
        ("TODO x\nsecond line", "TODO", "x\nsecond line"),
        ("plain text\nprio:: a", None, "plain text"),
        ("TODO a\nTODO b", "TODO", "a\nTODO b"),
        ("TODO\nx", "TODO", "x"),
        ("  TODO x\n\n", "TODO", "x"),
        ("TODO x", None, "TODO x"),
        ("TODO x", "", "TODO x"),
        ("TODO x", "IN-PROGRESS", "TODO x"),
    ])
    def test_task_text(self, content, marker, expected):
        assert tasks.task_text(content, marker) == expected


class TestTaskTextReadsTheSharedRule:
    """The lines that belong to a block are the ones blocktext says: a closed
    drawer, a planning line outside a code block. An opener nothing closes is
    text (mldoc 1.5.7), a drawer or planning line in a code fence is code, and
    a closer without an opener is text."""

    @pytest.mark.parametrize("content,expected", [
        ("TODO x\n:LOGBOOK:\nno end", "x\n:LOGBOOK:\nno end"),
        ("TODO x\n```\n:LOGBOOK:\n```\ny", "x\n```\n:LOGBOOK:\n```\ny"),
        ("TODO x\n```\nSCHEDULED: <2026-09-25 Fri>\n```",
         "x\n```\nSCHEDULED: <2026-09-25 Fri>\n```"),
        ("TODO x\n:END:\ny", "x\n:END:\ny"),
    ])
    def test_task_text(self, content, expected):
        assert tasks.task_text(content, "TODO") == expected


def _source_uses_the_shared_rule(path, function_name):
    """Whether the source (one function of it, or the whole module when
    function_name is None) calls attached_line_mask and keeps a rule of its
    own for the lines of a block out: no PLANNING_LINE_RE, no string holding
    the drawer opener. A docstring may name it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope = tree
    if function_name is not None:
        scope = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == function_name)
    docstrings = set()
    for node in ast.walk(scope):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    calls = own_pattern = False
    for node in ast.walk(scope):
        if isinstance(node, ast.Name) and node.id == "attached_line_mask":
            calls = True
        if isinstance(node, ast.Name) and node.id == "PLANNING_LINE_RE":
            own_pattern = True
        if isinstance(node, ast.Attribute) and node.attr == "PLANNING_LINE_RE":
            own_pattern = True
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings and ":LOGBOOK:" in node.value):
            own_pattern = True
    return calls and not own_pattern


PACKAGE = pathlib.Path(tasks.__file__).parent


class TestOneRuleForAttachedLines:
    @pytest.mark.parametrize("filename,function", [("tasks.py", "task_text")])
    def test_the_source_reads_the_shared_rule(self, filename, function):
        assert _source_uses_the_shared_rule(PACKAGE / filename, function)

    def test_the_check_catches_a_rule_of_its_own(self, tmp_path):
        own = tmp_path / "own.py"
        own.write_text('import re\n\ndef f(x):\n    return re.match(r"\\s*:LOGBOOK:", x)\n')
        assert not _source_uses_the_shared_rule(own, "f")
        shared = tmp_path / "shared.py"
        shared.write_text("def f(x):\n    return attached_line_mask(x)\n")
        assert _source_uses_the_shared_rule(shared, "f")


# --- the test helpers keep no marker rule of their own ------------------------

TESTS = pathlib.Path(__file__).parent
DOUBLE = "logseq_http_double.py"
ALLOWED_CALLER = ("LogseqHttpDouble", "_reparse_task_fields")
_RE_CALLS = {"compile", "match", "search", "sub", "findall", "fullmatch"}


def helper_files():
    """The helpers of the tests: every file in tests/ that is not a test."""
    return sorted(p for p in TESTS.glob("*.py")
                  if not p.name.startswith("test_") and p.name != "__init__.py")


def marker_lists(tree):
    return sites(tree)


def marker_regexes(tree):
    """Calls of re.compile/match/... with a string holding a marker word."""
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _RE_CALLS and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and WORD.search(arg.value):
                    found.append(node.lineno)
    return found


def old_names(tree):
    """The module name _MARKERS and a function _marker."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_marker":
            found.append(node.lineno)
        if isinstance(node, ast.Name) and node.id == "_MARKERS":
            found.append(node.lineno)
    return found


def marker_of_callers(tree):
    """(class, function) around each call of marker_of, by name or attribute."""
    found = []

    def visit(node, cls, func):
        if isinstance(node, ast.ClassDef):
            cls = node.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Name) and f.id == "marker_of") or \
                    (isinstance(f, ast.Attribute) and f.attr == "marker_of"):
                found.append((cls, func))
        for child in ast.iter_child_nodes(node):
            visit(child, cls, func)

    visit(tree, None, None)
    return found


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"))


class TestTestHelpersKeepNoMarkerRule:
    """The tests decide what a task is the way Logseq does: a double that
    derived the marker from the text with a rule of its own would confirm the
    code under test with the very rule it is to check. The helpers carry no
    marker list, no marker regex and the old names; only one method of the
    double reads the text for a marker, as Logseq does after a write."""

    def test_the_helpers_are_found(self):
        names = {p.name for p in helper_files()}
        assert {"conftest.py", DOUBLE} <= names

    @pytest.mark.parametrize("path", helper_files(), ids=lambda p: p.name)
    def test_no_marker_list(self, path):
        assert not marker_lists(_parse(path)), \
            f"{path.name} lists marker words; use tasks.ORDER or a fixture's own marker"

    @pytest.mark.parametrize("path", helper_files(), ids=lambda p: p.name)
    def test_no_marker_regex(self, path):
        assert not marker_regexes(_parse(path)), f"{path.name} matches a marker with a regex"

    @pytest.mark.parametrize("path", helper_files(), ids=lambda p: p.name)
    def test_no_old_names(self, path):
        assert not old_names(_parse(path)), f"{path.name} keeps _MARKERS or _marker"

    def test_marker_of_is_called_in_one_place(self):
        calls = []
        for path in helper_files():
            calls += [(path.name, *c) for c in marker_of_callers(_parse(path))]
        assert calls == [(DOUBLE, *ALLOWED_CALLER)]

    def test_the_guard_sees_a_list(self):
        assert marker_lists(ast.parse('X = ("TODO", "DONE")'))
        assert not marker_lists(ast.parse("from logseq_cli import tasks\nX = tasks.CLOCK_IN_STEPS\n"
                                          "Y = tasks.CLOCK_OUT_STEPS"))

    def test_the_guard_sees_a_regex(self):
        assert marker_regexes(ast.parse('import re\nre.compile(r"^(TODO|DONE) ")'))
        assert not marker_regexes(ast.parse('import re\nre.compile(r"^x")'))

    def test_the_guard_sees_the_old_names(self):
        assert old_names(ast.parse("def _marker(c):\n    pass"))
        assert old_names(ast.parse("_MARKERS = 1"))

    def test_the_guard_places_a_marker_of_call(self):
        elsewhere = ast.parse("class A:\n    def f(self):\n        return tasks.marker_of(x)")
        assert marker_of_callers(elsewhere) == [("A", "f")]
        here = ast.parse("class LogseqHttpDouble:\n    def _reparse_task_fields(self):\n"
                         "        return tasks.marker_of(x)")
        assert marker_of_callers(here) == [ALLOWED_CALLER]
