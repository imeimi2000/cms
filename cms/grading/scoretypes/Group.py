#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
from future.builtins.disabled import *  # noqa
from future.builtins import *  # noqa

from functools import reduce

from . import ScoreTypeGroup


# Dummy function to mark translatable string.
def N_(message):
    return message


class Group(ScoreTypeGroup):
    """Mode : -3 = GroupMin, -2 = GroupMul, -1 = GroupSum, >= 0 = GroupThreshold

    """

    def get_public_outcome(self, outcome, parameter):
        """See ScoreTypeGroup."""
        mode = parameter[2]
        if mode < 0.0:
            if outcome <= 0.0:
                return N_("Not correct")
            elif outcome >= 1.0:
                return N_("Correct")
            else:
                return N_("Partially correct")
        else:
            if 0.0 < outcome <= mode:
                return N_("Correct")
            else:
                return N_("Not correct")

    def reduce(self, outcomes, parameter):
        """See ScoreTypeGroup."""
        mode = parameter[2]
        if mode < 0.0:
            mode = -mode;
            if mode < 1.5:
                return reduce(lambda x, y: x + y, outcomes) / len(outcomes)
            elif mode < 2.5:
                return reduce(lambda x, y: x * y, outcomes)
            else:
                return min(outcomes)
        else:
            if all(0 < outcome <= mode for outcome in outcomes):
                return 1.0
            else:
                return 0.0
