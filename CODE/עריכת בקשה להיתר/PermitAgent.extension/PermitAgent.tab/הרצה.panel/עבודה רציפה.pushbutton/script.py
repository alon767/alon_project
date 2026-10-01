# -*- coding: utf-8 -*-
"""עבודה רציפה (מתג): כל עוד המצב פעיל, כל בקשת הרצה שסוכן משאיר בתיקייה "הערות\\auto\\queue" מורצת
אוטומטית ("הרצה מלאה" על הקומות שבבקשה, עם הקוד העדכני), והתוצאות נשמרות ב-"הערות\\auto".
לחיצה נוספת מכבה. רץ רק על עותק עבודה (_AGENT). בקשה מכילה רק שמות של קומות - לא קוד."""
__persistentengine__ = True

import io
import os
import sys
import json
import datetime
import traceback

TRACE = os.path.join(os.environ['APPDATA'], 'pyRevit', 'permit_write_trace.txt')
AUTO_DIR = os.path.join(os.environ['USERPROFILE'], u'OneDrive - אלון מיכאלי - אדריכל', u'ספריית ניסיונות',
                        u'עריכת בקשה להיתר', u'הערות', u'auto')
QUEUE_DIR = os.path.join(AUTO_DIR, u'queue')
STATUS_FILE = os.path.join(AUTO_DIR, u'watch_status.json')
KEY = 'PermitAgent.continuous'


def trace(msg):
    with io.open(TRACE, 'a', encoding='utf-8') as f:
        f.write(u'%s  %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), msg))


def status(state):
    try:
        s = json.dumps({u'state': state, u'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
                       ensure_ascii=False)
        with io.open(STATUS_FILE, 'w', encoding='utf-8') as fh:
            fh.write(s if isinstance(s, unicode) else s.decode('utf-8'))
    except Exception:
        pass


sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))
from System import AppDomain
from System.Threading import Timer, TimerCallback
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent, TaskDialog


class RunHandler(IExternalEventHandler):
    """runs the queued requests inside Revit's API context, with the newest installed code"""

    def __init__(self):
        self.busy = False

    def Execute(self, uiapp):
        if self.busy:
            return
        self.busy = True
        try:
            import permit_fullrun
            for m in permit_fullrun.RELOAD_ORDER:
                if m in sys.modules:
                    try:
                        reload(sys.modules[m])
                    except Exception as ex:
                        trace(u'continuous: reload %s failed: %s' % (m, ex))
            pf = sys.modules['permit_fullrun']
            n = 0
            while pf.pending_requests() and n < 20:
                pf.process_one(uiapp, trace)
                n += 1
        except Exception:
            trace(u'continuous: ERROR\n' + traceback.format_exc())
        finally:
            self.busy = False

    def GetName(self):
        return u'PermitAgent - עבודה רציפה'


state = AppDomain.CurrentDomain.GetData(KEY)
if state is not None:
    try:
        state[0].Dispose()
    except Exception:
        pass
    AppDomain.CurrentDomain.SetData(KEY, None)
    status(u'stopped')
    trace(u'=== continuous mode OFF')
    TaskDialog.Show(u'עבודה רציפה', u'מצב עבודה רציפה כובה.\nבקשות חדשות של הסוכנים לא יורצו עד שיופעל שוב.')
else:
    handler = RunHandler()
    event = ExternalEvent.Create(handler)
    beat = [datetime.datetime.now()]

    def tick(_):
        # a background thread: files only - Revit work is handed to the external event
        try:
            if not handler.busy:
                if os.path.isdir(QUEUE_DIR) and any(f.lower().endswith(u'.json') for f in os.listdir(QUEUE_DIR)):
                    event.Raise()
                elif (datetime.datetime.now() - beat[0]).total_seconds() > 60:
                    beat[0] = datetime.datetime.now()
                    status(u'waiting')
        except Exception:
            pass

    for d in (AUTO_DIR, QUEUE_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)
    timer = Timer(TimerCallback(tick), None, 3000, 5000)
    AppDomain.CurrentDomain.SetData(KEY, [timer, event, handler])
    status(u'waiting')
    trace(u'=== continuous mode ON')
    TaskDialog.Show(u'עבודה רציפה',
                    u'מצב עבודה רציפה פעיל.\n\n'
                    u'כל בקשת הרצה של סוכן תורץ אוטומטית על עותק העבודה, והתמונות יישמרו ב-"הערות\\auto".\n'
                    u'אפשר להמשיך לעבוד ברוויט כרגיל; ההרצה מתחילה כשרוויט פנוי.\n\n'
                    u'לכיבוי - ללחוץ שוב על הכפתור.')
