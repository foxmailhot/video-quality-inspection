from PySide6.QtCore import QObject,QEvent,Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QKeySequenceEdit,QMessageBox,QLineEdit,QAbstractSpinBox,QComboBox,QTextEdit,QPlainTextEdit)

DEFAULT_KEYS={'previous':'Left','next':'Right','play':'Space','open':'Ctrl+O'}
KEY_LABELS={'previous':'上一帧','next':'下一帧','play':'播放 / 暂停','open':'添加视频'}

def normalize_keys(data):
    result=DEFAULT_KEYS.copy()
    if isinstance(data,dict):
        for key in result:
            if isinstance(data.get(key),str):result[key]=QKeySequence.fromString(data[key],QKeySequence.PortableText).toString(QKeySequence.PortableText)
    values=[v for v in result.values() if v]
    if len(values)!=len(set(values)):return DEFAULT_KEYS.copy()
    return result

class ShortcutRouter(QObject):
    """Intercept keys before focused buttons, lists or the native video surface."""
    def __init__(self,window,callbacks):
        super().__init__(window);self.window=window;self.callbacks=callbacks;self.mapping=DEFAULT_KEYS.copy()
        QApplication.instance().installEventFilter(self)
    def eventFilter(self,watched,event):
        if event.type() not in (QEvent.KeyPress,QEvent.ShortcutOverride):return False
        if not self.window.isActiveWindow() or QApplication.activeModalWidget() is not None:return False
        focus=QApplication.focusWidget()
        if isinstance(focus,(QLineEdit,QAbstractSpinBox,QTextEdit,QPlainTextEdit,QKeySequenceEdit,QComboBox)):return False
        key=QKeySequence(event.keyCombination()).toString(QKeySequence.PortableText)
        action=next((name for name,value in self.mapping.items() if value and value==key),None)
        if action is None:return False
        event.accept()
        if event.type()==QEvent.KeyPress and (not event.isAutoRepeat() or action in ('previous','next')):
            self.callbacks[action]()
        return True

class ShortcutDialog(QDialog):
    def __init__(self,parent,mapping):
        super().__init__(parent);self.setWindowTitle('自定义快捷键');self.resize(460,330)
        layout=QVBoxLayout(self);title=QLabel('快捷键');title.setObjectName('title');layout.addWidget(title)
        note=QLabel('点击按键框，按下需要的按键或组合键。\n支持长按逐帧移动；编辑参数或弹出对话框时不触发。\n清空按键框可停用该快捷键。');note.setWordWrap(True);layout.addWidget(note)
        form=QFormLayout();self.editors={}
        for name,label in KEY_LABELS.items():
            editor=QKeySequenceEdit(QKeySequence(mapping[name]));editor.setMaximumSequenceLength(1);editor.setClearButtonEnabled(True)
            form.addRow(label,editor);self.editors[name]=editor
        layout.addLayout(form)
        row=QHBoxLayout();reset=QPushButton('恢复默认');reset.clicked.connect(self.reset);row.addWidget(reset);row.addStretch()
        cancel=QPushButton('取消');cancel.clicked.connect(self.reject);row.addWidget(cancel)
        save=QPushButton('保存');save.setObjectName('primary');save.clicked.connect(self.validate);row.addWidget(save);layout.addLayout(row)
    def reset(self):
        for name,editor in self.editors.items():editor.setKeySequence(QKeySequence(DEFAULT_KEYS[name]))
    def values(self):return {name:editor.keySequence().toString(QKeySequence.PortableText) for name,editor in self.editors.items()}
    def validate(self):
        values=self.values();used={}
        for name,key in values.items():
            if not key:continue
            if key in used:QMessageBox.warning(self,'快捷键冲突',f'“{KEY_LABELS[name]}”与“{KEY_LABELS[used[key]]}”使用了相同按键。');return
            if key in ('Alt+F4','Ctrl+Alt+Del') or 'Meta+' in key:QMessageBox.warning(self,'系统保留按键','请选择其他按键，避免与系统操作冲突。');return
            used[key]=name
        self.accept()
