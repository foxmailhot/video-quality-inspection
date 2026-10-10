"""Opt-in packaged UI regression check against the included 25 fps QC demo."""
from pathlib import Path
import tempfile,time,traceback,os
from PySide6.QtCore import QTimer,Qt
from PySide6.QtGui import QImage,QColor,QPainter,QLinearGradient
from appearance import BackgroundDialog,import_background
from engine import save_json

def run_check(app,window_type,video,report):
    report=Path(report);report.parent.mkdir(parents=True,exist_ok=True)
    temp=tempfile.TemporaryDirectory(prefix='framescope-release-')
    w=window_type();w.data_dir=Path(temp.name);w.clear_all();w.show()
    w.add_paths([str(Path(video).resolve())]);w.seek(.8);w.step_frame(1)
    state={'phase':0};start=time.monotonic();timer=QTimer();timer.setInterval(30)
    def tick():
        try:
            assert time.monotonic()-start<40,f'UI check timed out: phase={state["phase"]}, precise={w.precise_time}, player={w.player.position()}, status={w.player.mediaStatus()}, error={w.player.errorString()}, displayed={w.video.displayed_time}, converted={w.video.converter.converted}, worker={w.frame_worker is not None}, queue={len(w.frame_queue)}'
            if w.frame_worker is not None or w.frame_queue:return
            if state['phase']==0:
                assert abs(w.precise_time-.84)<1e-6,repr(w.precise_time)
                assert not w.frame_preview.image.isNull()
                w.step_frame(-1);state['phase']=1;return
            if state['phase']==1:
                assert abs(w.precise_time-.8)<1e-6,repr(w.precise_time)
                image=QImage(800,400,QImage.Format_RGB32);p=QPainter(image)
                gradient=QLinearGradient(0,0,800,400);gradient.setColorAt(0,QColor('#17425d'));gradient.setColorAt(1,QColor('#9c8174'))
                p.fillRect(image.rect(),gradient);p.setPen(QColor('#76bda9'))
                for x in range(0,800,40):p.drawLine(x,0,x,400)
                p.end();original=Path(temp.name)/'original.png';image.save(str(original))
                path=import_background(original,w.data_dir);original.unlink()
                w.apply_background(dict(mode='custom',custom_path=path,opacity=72,glass=True,fit='cover'))
                w.save_state();state['phase']=2;return
            if state['phase']==2:
                assert w.appearance['mode']=='custom' and not w.background.original.isNull()
                assert w.background.surface.toImage()!=w.background.blurred.toImage()
                assert [a.text() for a in w.settings_menu.actions()]==['快捷键','背景','检查更新']
                assert w.settings_button.menu() is w.settings_menu
                origin=lambda widget:widget.mapTo(w,widget.rect().topLeft()).y()
                assert origin(w.progress)<origin(w.video_stack)
                assert origin(w.play_slider)<origin(w.queue_bar)<origin(w.table)
                assert w.start.parent() is w.queue_bar and w.stop.parent() is w.queue_bar and w.badge.parent() is w.queue_bar
                w.grab().save(str(report.with_suffix('.png')))
                dialog=BackgroundDialog(w,w.appearance,w.data_dir);dialog.show();state['dialog']=dialog;state['phase']=3;return
            if state['phase']==3:
                dialog=state.pop('dialog');dialog.grab().save(str(report.with_name(report.stem+'-background.png')));dialog.reject()
                w.toggle_play();state['phase']=4;return
            if state['phase']==4:
                # Hidden installer checks do not receive normal paint events.
                # Wait for the prepared playback image, then force a real render.
                if w.video.image_time is None or w.video.image_time<1.2:return
                assert w.video.converter.converted>=5
                assert w.video.grab().toImage().pixelColor(1,1)==QColor('white')
                assert w.results_split.orientation()==Qt.Horizontal
                w.player.pause()
                w.clear_all();assert not w.jobs and not w.timeline.issues and w.queue.count()==0
                assert w.player.source().isEmpty() and w.frame_preview.image.isNull()
                save_json(report,dict(passed=True,frame_forward=.84,frame_backward=.8,managed_background=True,glass=True,clear_all=True,async_playback=True,horizontal_results=True,settings_menu=True,monitor_progress=True,queue_controls_between=True,cpu_preview_requested=os.environ.get('QT_FFMPEG_DECODING_HW_DEVICE_TYPES')==','))
                timer.stop();w.close();app.quit()
        except Exception:
            save_json(report,dict(passed=False,error=traceback.format_exc()));timer.stop();w.close();app.quit()
    timer.timeout.connect(tick);timer.start();app.exec()
    if w.frame_worker:w.frame_worker.requestInterruption();w.frame_worker.wait(5000)
    temp.cleanup()
