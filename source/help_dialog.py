"""Offline, selectable operation guide and current-file warnings."""
from html import escape
from PySide6.QtWidgets import QDialog,QVBoxLayout,QTextBrowser,QPushButton

class HelpDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.setWindowTitle('帮助 · FrameScope 视频质检');self.resize(720,680)
        layout=QVBoxLayout(self);self.browser=QTextBrowser();self.browser.setOpenExternalLinks(False);layout.addWidget(self.browser)
        done=QPushButton('关闭');done.clicked.connect(self.close);layout.addWidget(done)
    def update_guide(self,keys,warning=''):
        key=lambda name:escape(keys.get(name) or '未设置')
        self.browser.setHtml(f'''<html><body style="background:#172431;color:#e2eaf6;font-size:13px;line-height:150%">
<h2>视频质检操作指南</h2>
<h3>1. 添加与检测</h3><p>拖入视频文件即可添加，也可使用顶部“＋ 视频”或“文件夹”。在“检测参数”选择标准、严格或宽松档位，调整阈值后点击“开始队列”；“停止”会结束当前检测。参数对下一次检测生效。“重新检测”可重跑选中的视频，“一键清空列表”只清理队列和结果，不删除原视频。</p>
<h3>2. 定位与查看画面</h3><p>双击问题列表可跳到问题起点前1秒，不足1秒则跳到视频开头。播放进度条可点击或拖动；问题轨道也支持点击和拖拽播放头。上一帧、下一帧按钮可精确逐帧，鼠标放在“滚轮逐帧”上滚动，一格一帧。视频空白区域使用白底，方便检查黑边。</p>
<h3>3. 循环回看前后各1秒</h3><p>在问题详情旁点击“循环回看”，以点击时的播放头为中心，播放前1秒至后1秒并循环。范围固定，不随播放头移动而变化；靠近开头或结尾时自动限制在视频范围内。再次点击按钮或按空格即可停止并暂停。手动定位、逐帧、切换视频或清空列表会结束循环。循环时空格始终用于停止，即使已自定义播放快捷键。</p>
<h3>4. 人工复核与排序</h3><p>选中问题后可确认问题、标为误报、设为待复核或编辑。复核后选中下一个非误报问题。误报不显示在轨道上，确认问题以亮色白框突出。默认按时间排列并将误报置底；双击表头排序，再次双击切换方向。“默认排序”恢复初始规则。长依据可在下方详情或悬停提示中完整查看；窄列表可横向滚动。</p>
<h3>5. 黑边、监视器与倍速</h3><p>黑边轨道旁“忽略黑边”可全局隐藏队列中的黑边候选，导出也忽略；再次点击恢复，原始记录保留。监视器支持自动适配、手动高度、画面缩放与拖动画面；“适合窗口 / 居中”恢复居中。“专注监视器”放大检查画面，退出后可拖动分隔条调整轨道和问题列表。倍速按钮可滚轮调整0.1–10倍，点击恢复1倍。</p>
<h3>6. 日志、设置与更新</h3><p>“导出日志”可生成带单帧截图的HTML，可直接用浏览器打开；CSV适合Excel或WPS。可导入报告继续复核。右上角“设置”包含快捷键、背景与更新。顶部信息框显示检测状态和队列进度。视频和日志保留在本机，更新检查只访问版本信息及安装包。</p>
<h3>7. 当前快捷键</h3><p>播放 / 暂停：{key('play')}；上一帧：{key('previous')}；下一帧：{key('next')}；添加视频：{key('open')}。编辑输入框和对话框中不会触发常规播放快捷键。</p>
<h3>8. 检测结果的含义</h3><p>夹帧、定帧、花屏均为候选问题；正常转场、静止镜头和特效可能触发。单帧镜头可能是有意剪辑，提高敏感度会增加候选，需人工复核。黑帧和黑边是否属于错误也取决于作品要求。软件不做音频或专业HDR合规检测；检测失败不能视为通过。</p>
<h3>当前视频提示</h3><p>{escape(warning).replace(chr(10),'<br>') if warning else '当前没有额外提示。'}</p>
</body></html>''')
