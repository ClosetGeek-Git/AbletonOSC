import os as _os
import traceback as _tb

try:
    from .manager import Manager
except ImportError:
    #--------------------------------------------------------------------------------
    # Importing Manager pulls in `Live` / `ableton.v2`, which only exist inside Live --
    # so this legitimately fails under pytest, and we keep going so the package stays
    # importable for the headless tests.
    #
    # BUT the same swallow hides a genuine import bug when running INSIDE Live: Live then
    # only surfaces a cryptic downstream "NameError: name 'Manager' is not defined" from
    # create_instance(). So dump the real traceback next to this file for diagnosis.
    #--------------------------------------------------------------------------------
    try:
        with open(_os.path.join(_os.path.dirname(_os.path.realpath(__file__)), "import_error.log"), "w") as _f:
            _tb.print_exc(file=_f)
    except Exception:
        pass

def create_instance(c_instance):
    return Manager(c_instance)
