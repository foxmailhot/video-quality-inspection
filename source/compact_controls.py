from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import QSlider,QLabel,QSizePolicy,QStyle,QStyleOptionSlider


class SeekSlider(QSlider):
    hovered=Signal(int)
    def __init__(self):
        super().__init__(Qt.Horizontal);self.setMouseTracking(True);self.setFixedHeight(24);self.setMinimumWidth(140)
    def value_at(self,x):
        option=QStyleOptionSlider();self.initStyleOption(option)
        handle=self.style().pixelMetric(QStyle.PM_SliderLength,option,self)
        return QStyle.sliderValueFromPosition(self.minimum(),self.maximum(),round(x-handle/2),max(1,self.width()-handle),False)
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.setSliderDown(True);self.setValue(self.value_at(event.position().x()));self.hovered.emit(self.value());event.accept()
        else:super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        value=self.value_at(event.position().x())
        if self.isSliderDown():self.setValue(value)
        self.hovered.emit(value);event.accept()
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton and self.isSliderDown():
            self.setValue(self.value_at(event.position().x()));self.setSliderDown(False);event.accept()
        else:super().mouseReleaseEvent(event)


class CompactStatus(QLabel):
    def __init__(self,text=''):
        super().__init__();self.full_text='';self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed);self.setFixedHeight(24);self.setText(text)
    def setText(self,text):
        self.full_text=str(text);self.setToolTip(self.full_text)
        super().setText(self.fontMetrics().elidedText(self.full_text,Qt.ElideRight,max(1,self.width())))
    def resizeEvent(self,event):
        super().resizeEvent(event);self.setText(self.full_text)
