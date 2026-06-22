from PyQt6.QtWidgets import QMessageBox,QVBoxLayout,QGroupBox,QComboBox,QLabel,QApplication,QDialog,QTextEdit,QPushButton
from PyQt6.QtWidgets import QSlider, QDoubleSpinBox, QFormLayout, QHBoxLayout, QWidget, QGridLayout, QSpinBox, QScrollArea, QTabWidget
from helper import res_path,classlistToIds,updateListComboBox,qtBlockAndCall
from PyQt6.QtCore import QTime, QTimer
from PyQt6.QtCore import Qt,QMargins
from PyQt6.QtGui import QColor
import main
from base_ui import WidgetUI
from optionsdialog import OptionsDialog,OptionsDialogGroupBox

from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis, QBarSeries, QBarSet, QScatterSeries
from base_ui import CommunicationHandler
import math


ext_notice = """External encoder forwards the encoder
selection of the Axis (if available).
Please select the encoder there."""

hall_notice = """Using hall sensors as the main position
source is not recommended"""

aenc_notice = """Enter CPR as the amount of phases per 
revolution (Single pole SinCos = 1 CPR)"""

class TMC4671Ui(WidgetUI,CommunicationHandler):

    STATES = ["uninitialized","waitPower","Shutdown","Running","EncoderInit","EncoderFinished","HardError","OverTemp","IndexSearch","FullCalibration","ExternalEncoderInit","PI Autotune", "CoggingCalibration", "SlewRateCalibration", "NONE"]

    def __init__(self, main=None, unique=0):
        WidgetUI.__init__(self, main,'tmc4671_ui.ui')
        CommunicationHandler.__init__(self)
        self.axis = 0
        self.init_done = False
        self.main = main #type: main.MainUi

        self.axis = unique

        self.ui_initialized = False
        self.anti_coggingEnable = False
        self.cogging_data = [0] * 128
        self.cogging_data_received = [False] * 128
        self.cogging_supported = False
        self.cogging_calibrating = False
        self.cogging_dialog = None
        self.cogging_text = ""
        self.cogging_harmonics_data = []  # [(order, amplitude, phase), ...]
        self.cogging_position = 0.0  # normalized 0-1
        self.cogging_measured_torque = 0  # initialized before first acttrq update
        self.cogging_scale = 1.0  # default until MCU reports actual value
        self.max_datapoints = 10000
        self.max_datapointsVisibleTime = 30
        self.adc_to_amps = 0#2.5 / (0x7fff * 60.0 * 0.0015)

        self.hwversion = 0
        self.hwversions = []
        self.versionWarningShow = True
        self.vext = 0
        self.vint = 0
        self.vel_rpm = 0

        self.startTime = QTime.currentTime()
        self.chartLastX = 0

        self.timer = QTimer(self)
        self.timer_status = QTimer(self)
    
        self.pushButton_align.clicked.connect(self.alignEnc)
        self.pushButton_autotunepid.clicked.connect(self.autotunePid)
        self.pushButton_cogging.clicked.connect(self.coggingDetection)
        self.pushButton_resetCoggingTable.clicked.connect(self.resetCoggingTable)
        self.pushButton_resetCoggingTable.setVisible(False)
        self.pushButton_reloadCoggingTable.clicked.connect(self.reloadCoggingTable)
        self.pushButton_reloadCoggingTable.setVisible(False)
        self.tabWidget.currentChanged.connect(self.tabChanged)
        # Hide magnitude slider/spinbox (wired through .ui file)
        self.horizontalSlider_coggmag.hide()
        self.doubleSpinBox_coggScale.hide()
        #self.initUi()

        self.pushButton_scaleTune = QPushButton("Manual Tuning")
        self.pushButton_scaleTune.clicked.connect(self.openScaleCurveDialog)
        if hasattr(self, 'groupBox_anticogging'):
            formLayout = self.groupBox_anticogging.layout()
            if formLayout:
                formLayout.addRow(self.pushButton_scaleTune)

        self.timer.timeout.connect(self.updateTimer)
        self.timer_status.timeout.connect(self.updateStatus)

        # Reliable tab switch detection via main window's tab widget
        self.main.tabWidget_main.currentChanged.connect(self._on_main_tab_changed)

   
        # Chart setup
        self.chart = QChart()
        self.chart.setBackgroundRoundness(5)
        self.chart.setMargins(QMargins(0,0,0,0))
        self.chartXaxis = QValueAxis(self.chart)
        # use Application.instance().palette().dark().color() but with 50% opacity
        self.chartXaxis.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),128))
        

        self.chart.addAxis(self.chartXaxis,Qt.AlignmentFlag.AlignBottom)

        self.chartYaxis_Amps = QValueAxis(self.chart)
        self.chartYaxis_Temps = QValueAxis(self.chart)
        # use Application.instance().palette().dark().color() but with 25% opacity
        self.chartYaxis_Amps.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),64))
        self.chartYaxis_Temps.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),64))
        self.chart.setBackgroundBrush(QApplication.instance().palette().window())
        
        self.chart.addAxis(self.chartYaxis_Amps,Qt.AlignmentFlag.AlignLeft)
        
        self.lines_Amps = QLineSeries(self.chart)
        self.lines_Amps.setName("Torque A")
        self.lines_Amps.setUseOpenGL(True)

        self.chart.addSeries(self.lines_Amps)
        self.lines_Amps.setColor(QColor("cornflowerblue"))
        self.lines_Amps.attachAxis(self.chartYaxis_Amps)
        self.lines_Amps.attachAxis(self.chartXaxis)
        
        self.lines_Flux = QLineSeries(self.chart)
        self.lines_Flux.setName("Flux A")
        self.lines_Flux.setOpacity(0.5)
        self.lines_Flux.setUseOpenGL(True)
        
        self.chart.addSeries(self.lines_Flux)
        self.lines_Flux.setColor(QColor("limegreen"))

        self.lines_Flux.attachAxis(self.chartYaxis_Amps)
        self.lines_Flux.attachAxis(self.chartXaxis)
        
        self.lines_Cogging = QLineSeries(self.chart)
        self.lines_Cogging.setName("Cogging A")
        self.lines_Cogging.setOpacity(0.5)
        self.lines_Cogging.setUseOpenGL(True)
        self.chart.addSeries(self.lines_Cogging)
        self.lines_Cogging.setColor(QColor("purple"))
        self.lines_Cogging.attachAxis(self.chartYaxis_Amps)
        self.lines_Cogging.attachAxis(self.chartXaxis)
        
        self.lines_Temps = QLineSeries(self.chart)
        self.lines_Temps.setName("Temp °C")
        self.lines_Temps.setColor(QColor("orange"))
        self.lines_Temps.setOpacity(0.5)
        self.lines_Temps.setUseOpenGL(True)
        self.chart.addAxis(self.chartYaxis_Temps,Qt.AlignmentFlag.AlignRight)
        self.chart.addSeries(self.lines_Temps)
        self.lines_Temps.attachAxis(self.chartYaxis_Temps)
        self.lines_Temps.attachAxis(self.chartXaxis)
        self.chartYaxis_Temps.setMax(100)

        self.chartXaxis.setMax(10)
        self.chartYaxis_Amps.setMax(20)
        self.graphWidget_Amps.setRubberBand(QChartView.RubberBand.VerticalRubberBand)
        self.graphWidget_Amps.setChart(self.chart) # Set the chart widget

        # Set graph theme colors
        self.chart.legend().setVisible(False)

        # Cogging Chart setup
        self.chart_cogging = QChart()
        self.chart_cogging.setBackgroundRoundness(5)
        self.chart_cogging.setMargins(QMargins(0,0,0,0))
        self.chart_cogging_Xaxis = QValueAxis(self.chart_cogging)
        self.chart_cogging_Xaxis.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),128))
        self.chart_cogging.addAxis(self.chart_cogging_Xaxis,Qt.AlignmentFlag.AlignBottom)

        self.chart_cogging_Yaxis = QValueAxis(self.chart_cogging)
        self.chart_cogging_Yaxis.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),64))
        self.chart_cogging.setBackgroundBrush(QApplication.instance().palette().window())
        
        self.chart_cogging.addAxis(self.chart_cogging_Yaxis,Qt.AlignmentFlag.AlignLeft)
        
        self.bar_set_cogging = QBarSet("Harmonics")
        self.bar_series_cogging = QBarSeries()
        self.bar_series_cogging.append(self.bar_set_cogging)

        self.chart_cogging.addSeries(self.bar_series_cogging)
        self.bar_set_cogging.setColor(QColor("cornflowerblue"))
        self.bar_series_cogging.attachAxis(self.chart_cogging_Yaxis)
        self.bar_series_cogging.attachAxis(self.chart_cogging_Xaxis)

        self.chart_cogging_Xaxis.setRange(1, 128)
        self.chart_cogging_Yaxis.setMin(0)
        self.chart_cogging_Yaxis.setMax(10)
        self.graphWidget_Cogging.setRubberBand(QChartView.RubberBand.VerticalRubberBand)
        self.graphWidget_Cogging.setChart(self.chart_cogging)

        self.chart_cogging.legend().setVisible(False)

        # --- Anti-Cogging Profile Chart (position vs torque) ---
        self.chart_cogging_profile = QChart()
        self.chart_cogging_profile.setBackgroundRoundness(5)
        self.chart_cogging_profile.setMargins(QMargins(0,0,0,0))
        #self.chart_cogging_profile.setTitle("Anti-Cogging Profile")

        self.chart_cp_Xaxis = QValueAxis(self.chart_cogging_profile)
        self.chart_cp_Xaxis.setRange(0, 360)
        self.chart_cp_Xaxis.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),128))
        self.chart_cogging_profile.addAxis(self.chart_cp_Xaxis, Qt.AlignmentFlag.AlignBottom)

        self.chart_cp_Yaxis = QValueAxis(self.chart_cogging_profile)
        self.chart_cp_Yaxis.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),64))
        self.chart_cogging_profile.setBackgroundBrush(QApplication.instance().palette().window())
        self.chart_cogging_profile.addAxis(self.chart_cp_Yaxis, Qt.AlignmentFlag.AlignLeft)

        # Waveform line (combined anti-cogging)
        self.line_cp_waveform = QLineSeries(self.chart_cogging_profile)
        self.line_cp_waveform.setName("Combined")
        self.line_cp_waveform.setColor(QColor("limegreen"))
        pen = self.line_cp_waveform.pen()
        pen.setWidth(2)
        self.line_cp_waveform.setPen(pen)
        self.chart_cogging_profile.addSeries(self.line_cp_waveform)
        self.line_cp_waveform.attachAxis(self.chart_cp_Xaxis)
        self.line_cp_waveform.attachAxis(self.chart_cp_Yaxis)

        # CW raw waveform (red, semi-transparent)
        self.line_cp_cw = QLineSeries(self.chart_cogging_profile)
        self.line_cp_cw.setName("CW Raw")
        self.line_cp_cw.setColor(QColor("red"))
        self.line_cp_cw.setOpacity(0.6)
        self.chart_cogging_profile.addSeries(self.line_cp_cw)
        self.line_cp_cw.attachAxis(self.chart_cp_Xaxis)
        self.line_cp_cw.attachAxis(self.chart_cp_Yaxis)

        # CCW raw waveform (blue, semi-transparent)
        self.line_cp_ccw = QLineSeries(self.chart_cogging_profile)
        self.line_cp_ccw.setName("CCW Raw")
        self.line_cp_ccw.setColor(QColor("dodgerblue"))
        self.line_cp_ccw.setOpacity(0.6)
        self.chart_cogging_profile.addSeries(self.line_cp_ccw)
        self.line_cp_ccw.attachAxis(self.chart_cp_Xaxis)
        self.line_cp_ccw.attachAxis(self.chart_cp_Yaxis)

        # Measured Cogging Torque (orange dashed — motor's natural detent force)
        self.line_cp_cogging = QLineSeries(self.chart_cogging_profile)
        self.line_cp_cogging.setName("Cogging Torque")
        self.line_cp_cogging.setColor(QColor("darkorange"))
        pen = self.line_cp_cogging.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(1)
        self.line_cp_cogging.setPen(pen)
        self.line_cp_cogging.setOpacity(0.7)
        self.chart_cogging_profile.addSeries(self.line_cp_cogging)
        self.line_cp_cogging.attachAxis(self.chart_cp_Xaxis)
        self.line_cp_cogging.attachAxis(self.chart_cp_Yaxis)

        # --- Preview lines (grey dashed, show what Apply will produce) ---
        def _mk_preview(name, alpha=0.4):
            s = QLineSeries()
            s.setName(name)
            s.setColor(QColor("grey"))
            pen = s.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidth(1)
            s.setPen(pen)
            s.setOpacity(alpha)
            self.chart_cogging_profile.addSeries(s)
            s.attachAxis(self.chart_cp_Xaxis)
            s.attachAxis(self.chart_cp_Yaxis)
            return s

        self.line_cp_waveform_pv = _mk_preview("Anti-cog (scaled)")
        self.line_cp_cogging_pv  = _mk_preview("Cogging (scaled)")

        # Position dot
        self.scatter_cp_pos = QScatterSeries(self.chart_cogging_profile)
        self.scatter_cp_pos.setName("Position")
        self.scatter_cp_pos.setColor(QColor("red"))
        self.scatter_cp_pos.setMarkerSize(10)
        self.chart_cogging_profile.addSeries(self.scatter_cp_pos)
        self.scatter_cp_pos.attachAxis(self.chart_cp_Xaxis)
        self.scatter_cp_pos.attachAxis(self.chart_cp_Yaxis)

        # Vertical position line
        self.line_cp_vmarker = QLineSeries(self.chart_cogging_profile)
        self.line_cp_vmarker.setName("")
        self.line_cp_vmarker.setColor(QColor("red"))
        pen = self.line_cp_vmarker.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        self.line_cp_vmarker.setPen(pen)
        self.chart_cogging_profile.addSeries(self.line_cp_vmarker)
        self.line_cp_vmarker.attachAxis(self.chart_cp_Xaxis)
        self.line_cp_vmarker.attachAxis(self.chart_cp_Yaxis)

        self.chart_cogging_profile.legend().setVisible(False)

        self.graphWidget_Profile.setRubberBand(QChartView.RubberBand.RectangleRubberBand)
        self.graphWidget_Profile.setChart(self.chart_cogging_profile)

        # CW/CCW raw harmonic data (parsed from calibration broadcasts)
        self.cw_raw_harmonics = []  # [(order, mag, phase_rad), ...]
        self.ccw_raw_harmonics = []  # [(order, mag, phase_rad), ...]
        self.pot_scale = 1.0  # visualization-only preview scale (not sent to firmware)


        self.checkBox_advancedpid.stateChanged.connect(self.advancedPidChanged)
        self.lastPrecP = self.checkBox_P_Precision.isChecked()
        self.lastPrecI = self.checkBox_I_Precision.isChecked()
        self.buttonGroup_precision.buttonToggled.connect(self.changePrecision)

        self.pushButton_hwversion.clicked.connect(self.showVersionSelectorPopup)
        self.comboBox_mtype.currentIndexChanged.connect(self.motorselChanged)
        self.motor_type_to_index = {}
        self.comboBox_enc.currentIndexChanged.connect(self.encselChanged)
        self.encoder_type_to_index = {}

        self.checkBox_abnpol.stateChanged.connect(self.abnpolClicked)

        self.pushButton_calibrate.clicked.connect(lambda : self.send_command("tmc","calibrate",self.axis))
        self.checkBox_fluxdissipate.stateChanged.connect(lambda x : self.send_value("tmc","fluxbrake",val=1 if x else 0,instance=self.axis))

        # Messageboxes
        self.calmsg = QMessageBox()
        self.calmsg.setIcon(QMessageBox.Icon.Warning)
        self.calmsg.setWindowTitle(self.tr("Calibration required"))
        self.calmsg.setText(self.tr("A calibration of ADC offsets and encoder settings is required."))
        self.calmsg.setInformativeText(self.tr("Please set up the encoder and motor parameters correctly, apply power and start the full calibration by clicking OK or Cancel and start the calibration manually later once everything is set up.\n\nCertain ADC and encoder settings are stored in flash to accelerate the startup.\nIf a new board is used a new calibration must be done."))
        self.calmsg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)

        # Callbacks
        self.register_callback("tmc","temp",self.updateTemp,self.axis,int)
        self.register_callback("sys","vint",self.vintCb,0,int)
        self.register_callback("sys","vext",self.vextCb,0,int)
        self.register_callback("tmc","acttrq",self.updateCurrent,self.axis,str)

        self.register_callback("tmc","pidPrec",self.precisionCb,self.axis,int)
        self.register_callback("tmc","torqueP",self.spinBox_tp.setValue,self.axis,int)
        self.register_callback("tmc","torqueI",self.spinBox_ti.setValue,self.axis,int)
        self.register_callback("tmc","fluxP",self.spinBox_fp.setValue,self.axis,int)
        self.register_callback("tmc","fluxI",self.spinBox_fi.setValue,self.axis,int)
        self.register_callback("tmc","fluxoffset",lambda x : self.doubleSpinBox_fluxoffset.setValue(x*self.adc_to_amps),self.axis,int)
        self.register_callback("tmc","seqpi",self.checkBox_advancedpid.setChecked,self.axis,int)

        self.register_callback("tmc","tmctype",self.tmcChipTypeCB,self.axis,str,typechar='?')
        self.register_callback("tmc","state",self.stateCb,self.axis,str,typechar='?')

        self.register_callback("tmc","mtype",lambda x : self.comboBox_mtype.setCurrentIndex(self.motor_type_to_index.get(x,0)),self.axis,int)
        self.register_callback("tmc","poles",self.spinBox_poles.setValue,self.axis,int)
        self.register_callback("tmc","encsrc",lambda x : self.comboBox_enc.setCurrentIndex(self.encoder_type_to_index.get(x,0)),self.axis,int)
        self.register_callback("tmc","cpr",self.spinBox_cpr.setValue,self.axis,int)

        self.register_callback("tmc","iScale",self.setCurrentScaler,self.axis,float)

        self.register_callback("tmc","encsrc",self.encsCb,self.axis,str,typechar='!')
        self.register_callback("tmc","mtype",self.motsCb,self.axis,str,typechar='!')
        self.register_callback("tmc","tmcHwType",self.hwVersionsCb,self.axis,str,typechar='!')
        self.register_callback("tmc","tmcHwType",self.hwtcb,self.axis,int,typechar='?')
        self.register_callback("tmc","abnindex",self.checkBox_abnIndex.setChecked,self.axis,int,typechar='?')
        self.register_callback("tmc","abnpol",self.checkBox_abnpol.setChecked,self.axis,int,typechar='?')
        self.register_callback("tmc","combineEncoder",self.checkBox_combineEncoders.setChecked,self.axis,int,typechar='?')
        self.register_callback("tmc","invertForce",self.checkBox_invertForce.setChecked,self.axis,int,typechar='?')
        self.register_callback("tmc","svpwm",self.checkBox_svpwm.setChecked,self.axis,int,typechar='?')
        self.register_callback("tmc","fluxbrake",self.checkBox_fluxdissipate.setChecked,self.axis,int,typechar='?')

        self.filter_type_to_index = {}
        self.register_callback("tmc","trqbq_mode",self.filtersCb,self.axis,str,typechar='!')
        self.register_callback("tmc","trqbq_mode",self.comboBox_torqueFilter.setCurrentIndex,self.axis,int)
        self.register_callback("tmc","trqbq_f",self.spinBox_torqueFilterFreq.setValue,self.axis,int)
    
        self.register_callback("tmc","calibrated",self.calibrated,instance=self.axis,conversion=int)
        
        self.register_callback("tmc","coggingTable",self.updateCogging,self.axis,str)
        self.register_callback("tmc","calibrateCogging",self.coggingDetectionMsg,self.axis,str)
        self.register_callback("tmc","cogging",self.anticoggingStatus,self.axis,int,typechar='?')
        self.register_callback("tmc","coggingScale",self.coggingScaleCb,self.axis,int)
        self.register_callback("tmc","coggingShape",self.coggingShapeCb,self.axis,int)
        self.register_callback("tmc","coggingHarmonics",self.updateCoggingHarmonics,self.axis,str)
        self.register_callback("tmc","coggingCwCcw",self.updateCwCcwData,self.axis,str)
        
        self.checkBox_combineEncoders.stateChanged.connect(self.extEncoderChanged)


    def torqueFilterChanged(self,v):
        self.spinBox_torqueFilterFreq.setEnabled(v > 0)
        if v in self.filter_type_to_index:
            self.send_value("tmc","trqbq_mode",val=self.filter_type_to_index[v],instance=self.axis)



    # TODO do not send updates when window is moved. Blocks serial port receive on windows
    def _on_main_tab_changed(self, index):
        """Reliable tab switch — starts/stops timers based on visibility."""
        if self.main.tabWidget_main.widget(index) is self:
            if not self.ui_initialized:
                self.init_ui()
            if self.isEnabled() and not self.cogging_calibrating:
                self.timer.start(50)
                self.timer_status.start(250)
        else:
            self.timer.stop()
            self.timer_status.stop()

    def coggingSupportedCb(self, info):
        self.cogging_supported = (info != -1)
        self.updateMotorUI()
        
    def anticoggingStatus(self, data):
        self.anti_coggingEnable = (data == 1)
        self.updateMotorUI()

    def motorselChanged(self, val):
        self.updateMotorUI()

    def updateMotorUI(self):
        data = self.comboBox_mtype.currentData()
        supported_motor = (data == 2 or data == 3) # stepper or bldc

        self.spinBox_poles.setEnabled(supported_motor)
        self.doubleSpinBox_fluxoffset.setEnabled(supported_motor)
        self.checkBox_fluxdissipate.setEnabled(supported_motor)
        self.pushButton_autotunepid.setEnabled(supported_motor)
        
        # Cogging visibility depends on motor support AND firmware command existence
        cogging_enabled = supported_motor and self.cogging_supported
        self.pushButton_cogging.setEnabled(cogging_enabled)
        self.checkBox_cogging.setEnabled(cogging_enabled)
        self.groupBox_anticogging.setEnabled(cogging_enabled)
        self.tabWidget.setTabEnabled(1, cogging_enabled)
        self.syncHarmonicEditor()


        # If anti-cogging was enabled, notify user and disable it before graying out
        self.checkBox_cogging.setChecked(self.anti_coggingEnable)
        if self.anti_coggingEnable:
            if not supported_motor:
                #msg = QMessageBox(QMessageBox.Icon.Information,self.tr("Anti-Cogging"),self.tr("Auto-disabling Anti-Cogging on this motor"))
                #msg.exec()
                self.checkBox_cogging.setChecked(False)

        if(data == 3):
            self.checkBox_svpwm.setEnabled(True)
        else:
            self.checkBox_svpwm.setEnabled(False)

    def extEncoderChanged(self,idx):
        val = self.comboBox_mtype.currentData()
        self.checkBox_invertForce.setEnabled(val)
        if not val:
            self.checkBox_invertForce.setChecked(False)
        else:
            self.send_command("tmc","invertForce",self.axis)


    def abnpolClicked(self,val):
        if val:
            self.checkBox_abnpol.setText("ABN polarity (HIGH)")
        else:
            self.checkBox_abnpol.setText("ABN polarity (LOW)")

    def encselChanged(self,val):
        data = self.comboBox_enc.currentData()
        self.checkBox_abnIndex.setVisible(data == 1) # abnIndex selectable if ABN encoder selected

        self.checkBox_abnpol.setVisible(data == 1)
        
        if(data == 5):
            self.label_encoder_notice.setText(ext_notice)
        if(data == 4):
            self.label_encoder_notice.setText(hall_notice)
        if(data == 2 or data == 3):
            self.label_encoder_notice.setText(aenc_notice)


        self.label_encoder_notice.setVisible(data == 5 or data == 4 or data == 3 or data == 2) # Visible for ext, hall and aenc
        #self.checkBox_abnpol.setEnabled(data == 1)

        self.spinBox_cpr.setVisible(data == 1 or data == 2 or data == 3)
        self.label_cpr.setVisible(data == 1 or data == 2 or data == 3)

        self.checkBox_combineEncoders.setVisible(data == 1 or data == 2 or data == 3 or data == 4)
        self.checkBox_invertForce.setVisible(data == 1 or data == 2 or data == 3 or data == 4)
        self.checkBox_invertForce.setEnabled(self.checkBox_combineEncoders.isChecked())
        

    def updateCurrent(self,torqueflux):
        tflist = [(int(v)) for v in torqueflux.split(":")]
        
        flux = None
        cogging = None
        pos = None
        torque = abs(tflist[0])
        if len(tflist) >= 2:
            flux = tflist[1]
        if len(tflist) >= 3:
            cogging = tflist[2]
            self.cogging_measured_torque = cogging
        if len(tflist) >= 4:
            self.cogging_scale = int(tflist[3]) / 100.0  # scale*100 from MCU (read-only)
        if len(tflist) >= 5:
            pos = tflist[4] / 10000.0  # normalized 0-1
            self.cogging_position = pos
            self.updateProfilePosition()
        vel_rpm = 0
        if len(tflist) >= 6:
            vel_rpm = int(tflist[5])  # velocity RPM from MCU
        self.vel_rpm = vel_rpm
            
        currents = complex(torque, flux if flux is not None else 0)
        try:
            torque = abs(float(torque))
            
            if self.adc_to_amps != 0:
                amps = currents * self.adc_to_amps
                txt = f"Torque: {amps.real:+.3f}A"
                
                total_amps = abs(amps.real)
                if flux is not None:
                    txt += f"\nFlux: {amps.imag:+.3f}A"
                    total_amps += abs(amps.imag)
                if cogging is not None:
                    c_amps = cogging * self.adc_to_amps
                    txt += f"\nCogging: {c_amps:+.3f}A"
                    total_amps += abs(c_amps)
                if flux is not None or cogging is not None:
                    txt += f"\nTotal: {total_amps:.3f}A"
                
                self.label_Current.setText(txt)

            else:
                amps = 100*currents / 0x7fff # percent
                txt = str(round(amps.real,3))+"%"
                self.label_Current.setText(txt)
                
            self.progressBar_power.setValue(int(abs(currents)))

            self.chartLastX = self.startTime.msecsTo(QTime.currentTime()) / 1000
            self.lines_Amps.append(self.chartLastX,amps.real)
            self.lines_Flux.append(self.chartLastX,abs(amps.imag))
            
            cogging_val = 0
            if cogging is not None:
                if self.adc_to_amps != 0:
                    cogging_val = cogging * self.adc_to_amps
                else:
                    cogging_val = 100 * cogging / 0x7fff
                self.lines_Cogging.append(self.chartLastX, cogging_val)
            
            if(self.lines_Amps.count() > self.max_datapoints):
                self.lines_Amps.remove(0)
                self.lines_Flux.remove(0)
                
            if self.lines_Cogging.count() > self.max_datapoints:
                self.lines_Cogging.remove(0)
                
            scalemax = max(abs(amps.imag), abs(amps.real), abs(cogging_val))
            if(scalemax > self.chartYaxis_Amps.max()):
                self.chartYaxis_Amps.setMax(round(scalemax,2)) # increase range
                
            if cogging_val < self.chartYaxis_Amps.min():
                self.chartYaxis_Amps.setMin(round(cogging_val, 2)) # increase range downwards

            self.chartXaxis.setMax(self.chartLastX)
            self.chartXaxis.setMin(max(self.lines_Amps.at(0).x(),max(0,self.chartLastX-self.max_datapointsVisibleTime)))

        except Exception as e:
            self.main.log("TMC update error: " + str(e))

    def updateCogging(self,data):
        try:
            if "data" in data:
                # Correctly parse the "item:X,data:(Y,Z,...)" format
                item_str, data_str = data.split(',', 1)
                start_index = int(item_str.split(':')[1])
                
                # Extract the numbers from within the parentheses
                values_str = data_str.split('(')[1].split(')')[0]
                points = [float(p) for p in values_str.split(',') if p]

                for i, p in enumerate(points):
                    if start_index + i < len(self.cogging_data):
                        self.cogging_data[start_index + i] = p
                        self.cogging_data_received[start_index + i] = True
                
                # Redraw the entire graph with the updated data
                self.bar_set_cogging.remove(0, self.bar_set_cogging.count())
                # Add a dummy zero at index 0 so that harmonics 1-128 align with X axis values 1-128
                self.bar_set_cogging.append(0.0)
                self.bar_set_cogging.append(self.cogging_data)

                self.chart_cogging_Xaxis.setRange(1, 128)
                
                valid_data = [p for i, p in enumerate(self.cogging_data) if self.cogging_data_received[i]]
                if valid_data:
                    self.chart_cogging_Yaxis.setMax(max(10, max(valid_data)))
                    self.chart_cogging_Yaxis.setMin(0)

        except Exception as e:
            self.main.log("TMC cogging update error: " + str(e))

    def updateTemp(self,t):
        t = t/100.0
        if(t > 150 or t < -20):
            return
        self.label_Temp.setText(str(round(t,2)) + "°C")

        # Amps updates faster and gives the current timestamp
        self.lines_Temps.append(self.chartLastX+1,t)
        if(self.lines_Temps.count() > self.max_datapoints):
            self.lines_Temps.remove(0)

        
        if(t > self.chartYaxis_Temps.max()):
            self.chartYaxis_Temps.setMax(round(t)) # increase range
    
    def updateVolt(self):
        t = "Mot: {:2.2f}V".format(self.vint)
        t += "\nIn: {:2.2f}V".format(self.vext)
        self.label_volt.setText(t)

    def vintCb(self,v):
        self.vint = v/1000

    def vextCb(self,v):
        self.vext = v/1000
        self.updateVolt()

    def stateCb(self,state):
        intstate = int(state)
        if(len(self.STATES) > intstate):
            self.label_state.setText(self.STATES[intstate])
        else:
            self.label_state.setText(state)

    def updateTimer(self):
        self.send_command("tmc","acttrq",self.axis)
        
        
    def updateStatus(self):
        self.send_command("tmc","temp",self.axis)
        self.send_command("tmc","state",self.axis)
        self.send_commands("sys",["vint","vext"])

    def submitMotor(self):
        mtype = self.comboBox_mtype.currentData()
        self.send_value("tmc","mtype",val=mtype,instance=self.axis)

        poles = self.spinBox_poles.value()
        self.send_value("tmc","poles",val=poles,instance=self.axis)

        self.send_value("tmc","cpr",val=self.spinBox_cpr.value(),instance=self.axis)

        enc = self.comboBox_enc.currentData()
        self.send_value("tmc","encsrc",val=enc,instance=self.axis)

        self.send_value("tmc","abnindex",val = 1 if self.checkBox_abnIndex.isChecked() else 0,instance=self.axis)
        self.send_value("tmc","abnpol",val = 1 if self.checkBox_abnpol.isChecked() else 0,instance=self.axis)

        self.send_value("tmc","combineEncoder",val = 1 if self.checkBox_combineEncoders.isChecked() else 0,instance=self.axis)
        self.send_value("tmc","invertForce",val = 1 if self.checkBox_invertForce.isChecked() else 0,instance=self.axis)
        self.send_value("tmc","cogging",val = 1 if self.checkBox_cogging.isChecked() else 0,instance=self.axis)
    def submitPid(self):
        # PIDs
        seq = 1 if self.checkBox_advancedpid.isChecked() else 0
        self.send_value("tmc","seqpi",val=seq,instance=self.axis)

        tp = self.spinBox_tp.value()
        self.send_value("tmc","torqueP",val=tp,instance=self.axis)

        ti = self.spinBox_ti.value()
        self.send_value("tmc","torqueI",val=ti,instance=self.axis)

        fp = self.spinBox_fp.value()
        self.send_value("tmc","fluxP",val=fp,instance=self.axis)

        fi = self.spinBox_fi.value()
        self.send_value("tmc","fluxI",val=fi,instance=self.axis)

        prec = self.checkBox_I_Precision.isChecked() | (self.checkBox_P_Precision.isChecked() << 1)
        self.send_value("tmc","pidPrec",val=prec,instance=self.axis)
        self.send_value("tmc","svpwm",val=1 if self.checkBox_svpwm.isChecked() else 0,instance=self.axis)
        
    def changePrecision(self,button,checked):
        rescale = (16 if checked else 1/16)
        if(button == self.checkBox_I_Precision):
            if(self.lastPrecI != checked):
                self.spinBox_ti.setValue(int(self.spinBox_ti.value() * rescale))
                self.spinBox_fi.setValue(int(self.spinBox_fi.value() * rescale))
        if(button == self.checkBox_P_Precision):
            if(self.lastPrecP != checked):
                self.spinBox_tp.setValue(int(self.spinBox_tp.value() * rescale))
                self.spinBox_fp.setValue(int(self.spinBox_fp.value() * rescale))

        self.lastPrecP = self.checkBox_P_Precision.isChecked()
        self.lastPrecI = self.checkBox_I_Precision.isChecked()

    def precisionCb(self,val):
        self.checkBox_I_Precision.setChecked(val & 0x1)
        self.checkBox_P_Precision.setChecked(val & 0x2)

    def advancedPidChanged(self,state):
        self.checkBox_P_Precision.setEnabled(state)
        self.checkBox_I_Precision.setEnabled(state)
        if(state):
            pass
        else:
            self.checkBox_P_Precision.setChecked(False)
            self.checkBox_I_Precision.setChecked(False)
   
    def showVersionSelectorPopup(self):
        selectorPopup = OptionsDialog(TMC_HW_Version_Selector(self.tr("TMC Version"),self,self.axis),self)
        selectorPopup.exec()
        self.send_command("tmc","tmcHwType",self.axis,'!')
        self.send_command("tmc","tmcHwType",self.axis,'?')
       
    def hwVersionsCb(self,v):
        entriesList = v.split("\n")
        entriesList = [m.split(":") for m in entriesList if m]
        self.hwversions = {int(entry[0]):entry[1] for entry in entriesList}

    def hwtcb(self,t):
        self.hwversion = int(t)
        
        self.label_hwversion.setText(self.hwversions[self.hwversion])
        if self.hwversion == 0 and self.versionWarningShow and len(self.hwversions) > 0:
            # no version set. ask user to select version
            self.versionWarningShow = False
            QTimer.singleShot(100,self.showVersionSelectorPopup) # return this function but show popup with a tiny delay
             
        else:
            self.versionWarningShow = False

    def init_ui(self):
        # clear graph
        self.startTime = QTime.currentTime()
        self.chartLastX = 0
        self.lines_Amps.clear()
        self.lines_Temps.clear()
        self.lines_Flux.clear()
        self.lines_Cogging.clear()
        self.clearCoggingGraph()
        self.chartYaxis_Amps.setMin(0)
        self.chartYaxis_Temps.setMin(0)
        self.chartYaxis_Temps.setMax(90)
        try:
            # Fill encoder source types
            self.send_commands("tmc",["mtype","encsrc","tmcHwType","trqbq_mode"],self.axis,'!')
            self.send_commands("tmc",["tmctype","tmcHwType","iScale","calibrated","trqbq_f","coggingScale","coggingShape"],self.axis)
            self.send_command("tmc","cogging",self.axis,'?')

            # Check if cogging is supported
            self.get_value_async("tmc", "cmdinfo", self.coggingSupportedCb, self.axis, conversion=int, adr=44)
            self.getMotor()
            self.getPids()
            if not self.init_done:
                self.doubleSpinBox_fluxoffset.valueChanged.connect(lambda v : self.send_value("tmc","fluxoffset",v/self.adc_to_amps,instance=self.axis))
                self.pushButton_submitmotor.clicked.connect(self.submitMotor)
                self.pushButton_submitpid.clicked.connect(self.submitPid)
                self.comboBox_torqueFilter.currentIndexChanged.connect(self.torqueFilterChanged)
                self.spinBox_torqueFilterFreq.valueChanged.connect(lambda x : self.send_value("tmc","trqbq_f",x,instance=self.axis))
                self.init_done = True

            # Check if calibrated
            if self.tabWidget.currentWidget() == self.tab_6:
                self.reloadCoggingTable()
            else:
                # Pre-fetch cogging data even when on a different sub-tab
                self.send_command("tmc", "coggingHarmonics", self.axis, '?')
            self.ui_initialized = True
        except Exception as e:
            self.main.log("Error initializing TMC tab. Please reconnect: " + str(e))
            return False
        return True

    def tmcChipTypeCB(self,type : str):
        if not type.startswith("TMC"):
            self.main.log("Can not find TMC")
            self.groupBox_tmc.setTitle("Driver (not connected)")
            self.setEnabled(False)
            self.timer.stop()
            self.timer_status.stop()
            self.ui_initialized = False
        else:
            self.groupBox_tmc.setTitle(type)
            self.setEnabled(True)

    def calibrated(self,v):
        v = int(v)
        if not v and self.isEnabled() and self.comboBox_mtype.currentIndex() != 0 and self.comboBox_enc.currentIndex() != 0:
            # Warning displayed
            def cb(ret):
                if ret == QMessageBox.StandardButton.Ok:
                    self.send_command("tmc","calibrate",self.axis)
            self.calmsg.finished.connect(cb)
            self.calmsg.open()


    def encsCb(self,encsrcs):
        updateListComboBox(combobox=self.comboBox_enc,reply=encsrcs,dataSep="=",lookup=self.encoder_type_to_index,dataconv=int)

    def filtersCb(self,filters):
        updateListComboBox(combobox=self.comboBox_torqueFilter,reply=filters,dataSep="=",lookup=self.filter_type_to_index,dataconv=int)
        self.send_command("tmc","trqbq_mode",self.axis)

    def motsCb(self,mots):
        updateListComboBox(combobox=self.comboBox_mtype,reply=mots,dataSep="=",lookup=self.motor_type_to_index,dataconv=int)

    def autotunePid(self):
        self.pushButton_autotunepid.setEnabled(False)
        def f(res):
            self.pushButton_autotunepid.setEnabled(True)
            if(res):
                msg = QMessageBox(QMessageBox.Icon.Information,"PID autotuning",res)
                msg.exec()
            self.getPids()

        self.get_value_async("tmc","pidautotune",f,self.axis,typechar='?')
        self.main.log("Started PID tuning")

    def alignEnc(self):
        self.pushButton_align.setEnabled(False)
        def f(res):
            self.pushButton_align.setEnabled(True)
            if(res):
                msg = QMessageBox(QMessageBox.Icon.Information,"Encoder align",res)
                msg.exec()

        self.get_value_async("tmc","encalign",f,self.axis,typechar='?')
        self.main.log("Started encoder alignment")
        
    def coggingDetectionMsg(self, data):
        if data:
            msg_text = str(data)
            status = 0
            # Parsing du nouveau format: ("message de log",0)
            if msg_text.startswith('("') and msg_text.endswith(')'):
                parts = msg_text.rsplit('",', 1)
                if len(parts) == 2:
                    text_part = parts[0][2:] # Remove '("'
                    try:
                        status = int(parts[1][:-1]) # Remove ')'
                        msg_text = text_part
                    except ValueError:
                        pass
            
            # Append new message to the accumulated text
            if self.cogging_text:
                self.cogging_text += "\n"
            self.cogging_text += msg_text
            
            if self.cogging_dialog is None:
                # Create and show a resizable QDialog with a QTextEdit
                self.cogging_dialog = QDialog(self)
                self.cogging_dialog.setWindowTitle(self.tr("Cogging calibration"))
                self.cogging_dialog.setMinimumSize(500, 400)
                
                layout = QVBoxLayout(self.cogging_dialog)
                
                self.cogging_text_edit = QTextEdit()
                self.cogging_text_edit.setReadOnly(True)
                layout.addWidget(self.cogging_text_edit)
                
                close_btn = QPushButton(self.tr("Close"))
                close_btn.clicked.connect(self.cogging_dialog.close)
                layout.addWidget(close_btn)
                
                self.cogging_dialog.show()
                # Reset state when closed
                def on_finish():
                    self.cogging_dialog = None
                    self.cogging_text = ""
                    self.cogging_text_edit = None
                    if not self.cogging_calibrating:
                        self.timer.start(50)
                        self.timer_status.start(250)
                self.cogging_dialog.finished.connect(on_finish)
            
            # Update text and scroll to bottom
            self.cogging_text_edit.setText(self.cogging_text)
            self.cogging_text_edit.verticalScrollBar().setValue(self.cogging_text_edit.verticalScrollBar().maximum())
            
            # Automatically handle end of calibration
            if status == 1:
                self.cogging_calibrating = False
                self.pushButton_cogging.setEnabled(True)
                self.timer.start(50)
                self.timer_status.start(250)
                self.reloadCoggingTable()
                self.send_command("tmc", "cogging", self.axis, '?')
                # Also fetch raw CW/CCW data now that calibration finished
                self.send_command("tmc", "coggingCwCcw", self.axis, '?')

            # Parse CW/CCW raw harmonic data from calibration log
            # Messages are broadcast with prefixes CWD: or CCWD:
            if msg_text.startswith("CWD:") or msg_text.startswith("CCWD:"):
                try:
                    is_cw = msg_text.startswith("CWD:")
                    prefix = "CWD:" if is_cw else "CCWD:"
                    data_str = msg_text[len(prefix):]
                    target_list = self.cw_raw_harmonics if is_cw else self.ccw_raw_harmonics

                    for chunk in data_str.split(","):
                        chunk = chunk.strip()
                        if not chunk:
                            continue
                        parts = chunk.split(":")
                        if len(parts) == 3:
                            order = int(parts[0])
                            mag = float(parts[1])
                            phase = float(parts[2]) / 1000.0  # rad*1000 from firmware
                            if mag > 0.0:
                                # Update existing or append
                                found = False
                                for i, (o, m, p) in enumerate(target_list):
                                    if o == order:
                                        target_list[i] = (order, mag, phase)
                                        found = True
                                        break
                                if not found:
                                    target_list.append((order, mag, phase))
                    self.rebuildCwCcwWaveforms()
                except Exception:
                    pass  # silently ignore parse errors in calibration log
        
    def coggingDetection(self):
        self.pushButton_cogging.setEnabled(False)
        self.cogging_calibrating = True
        self.timer.stop()
        self.timer_status.stop()
        self.send_command("tmc","calibrateCogging", self.axis)
        self.main.log("Started cogging detection")

    def tabChanged(self, index):
        # Automatically reload the cogging table when its tab is selected
        if self.tabWidget.widget(index) == self.tab_6:
            self.reloadCoggingTable()

    def clearCoggingGraph(self):
        self.bar_set_cogging.remove(0, self.bar_set_cogging.count())
        self.cogging_data = [0] * 128
        self.cogging_data_received = [False] * 128
        # Reset axes to default values
        self.chart_cogging_Yaxis.setMin(0)
        self.chart_cogging_Yaxis.setMax(10)
        self.clearCoggingProfile()

    def clearCoggingProfile(self):
        self.cogging_harmonics_data = []
        self.cw_raw_harmonics = []
        self.ccw_raw_harmonics = []
        self.line_cp_waveform.clear()
        self.line_cp_cw.clear()
        self.line_cp_ccw.clear()
        self.line_cp_cogging.clear()
        self.line_cp_waveform_pv.clear()
        self.line_cp_cogging_pv.clear()
        self.scatter_cp_pos.clear()
        self.line_cp_vmarker.clear()
        self.chart_cp_Yaxis.setMin(-10)
        self.chart_cp_Yaxis.setMax(10)
        self.syncHarmonicEditor()

    def syncHarmonicEditor(self):
        pass

    def updateHarmonicPreview(self):
        if not self.cogging_harmonics_data:
            self.bar_set_cogging.remove(0, self.bar_set_cogging.count())
            self.line_cp_waveform.clear()
            self.line_cp_cogging.clear()
            self.scatter_cp_pos.clear()
            self.line_cp_vmarker.clear()
            return

        self.bar_set_cogging.remove(0, self.bar_set_cogging.count())
        self.bar_set_cogging.append(0.0)

        bars = [0.0] * 128
        for order, amp, _phase in self.cogging_harmonics_data:
            if 1 <= int(order) <= 128:
                bars[int(order) - 1] = float(amp)

        self.bar_set_cogging.append(bars)
        self.chart_cogging_Xaxis.setRange(1, 128)

        valid_data = [amp for _order, amp, _phase in self.cogging_harmonics_data if amp > 0]
        if valid_data:
            self.chart_cogging_Yaxis.setMax(max(10, max(valid_data)))
            self.chart_cogging_Yaxis.setMin(0)

        self.rebuildProfileWaveform()



    def updateCoggingHarmonics(self, data):
        """Parse coggingHarmonics reply: 'order:amp:phase,...' and rebuild waveform."""
        try:
            if not data or data == "0:0:0":
                self.cogging_harmonics_data = []
                self.updateHarmonicPreview()
                self.syncHarmonicEditor()
                return
            
            harmonics = []
            for item in data.split(","):
                parts = item.split(":")
                if len(parts) == 3:
                    order = int(parts[0])
                    amp = float(parts[1])
                    phase = float(parts[2]) / 1000.0  # phase * 1000 from firmware
                    if order > 0 or amp > 0:
                        harmonics.append((order, amp, phase))
            
            self.cogging_harmonics_data = harmonics
            self.syncHarmonicEditor()
            self.updateHarmonicPreview()
        except Exception as e:
            self.main.log("TMC cogging harmonics parse error: " + str(e))

    def updateCwCcwData(self, data):
        """Parse coggingCwCcw reply: 'CW:order:amp:phase,...|CCW:order:amp:phase,...'"""
        try:
            if not data:
                return
            self.cw_raw_harmonics = []
            self.ccw_raw_harmonics = []

            parts = data.split("|")
            for part in parts:
                if part.startswith("CW:"):
                    target = self.cw_raw_harmonics
                    data_str = part[3:]
                elif part.startswith("CCW:"):
                    target = self.ccw_raw_harmonics
                    data_str = part[4:]
                else:
                    continue

                if data_str == "0:0:0":
                    continue

                for item in data_str.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    segs = item.split(":")
                    if len(segs) == 3:
                        order = int(segs[0])
                        amp = float(segs[1])
                        phase = float(segs[2]) / 1000.0
                        if amp > 0.0:
                            target.append((order, amp, phase))

            self.rebuildCwCcwWaveforms()
        except Exception as e:
            self.main.log("TMC CW/CCW parse error: " + str(e))

    def rebuildProfileWaveform(self):
        """Draw green (anti-cogging sin), orange (cogging potential cos)."""
        self.line_cp_waveform.clear()
        self.line_cp_cogging.clear()
        self.line_cp_waveform_pv.clear()
        self.line_cp_cogging_pv.clear()
        if not self.cogging_harmonics_data:
            return

        s = self.pot_scale
        show_pv = abs(s - 1.0) > 0.005

        max_amp = 0.0
        for deg in range(0, 361):
            theta = math.radians(deg)
            green = 0.0
            orange = 0.0
            green_pv = 0.0
            orange_pv = 0.0
            for order, amp, phase in self.cogging_harmonics_data:
                # Main lines: firmware amplitude (no pot_scale)
                green  += amp * math.sin(order * theta + phase)
                orange -= amp * math.cos(order * theta + phase)
                # Scaled previews
                if show_pv:
                    a_s = amp * s
                    green_pv  += a_s * math.sin(order * theta + phase)
                    orange_pv -= a_s * math.cos(order * theta + phase)
            self.line_cp_waveform.append(float(deg), float(green))
            self.line_cp_cogging.append(float(deg), float(orange))
            if show_pv:
                self.line_cp_waveform_pv.append(float(deg), float(green_pv))
                self.line_cp_cogging_pv.append(float(deg), float(orange_pv))
                max_amp = max(max_amp, abs(green_pv), abs(orange_pv))
            max_amp = max(max_amp, abs(green), abs(orange))

        margin = max(max_amp * 1.2, 10.0)
        self.chart_cp_Yaxis.setRange(-margin, margin)
        self.updateProfilePosition()

    def onHarmonicMagnitudeChanged(self, index, value):
        pass

    def on_pot_scale_changed(self, _val=None):
        self.pot_scale = 1.0  # visualization-only, always 1.0 now
        self.rebuildProfileWaveform()

    def rebuildCwCcwWaveforms(self):
        """Rebuild CW (red) and CCW (blue) raw waveforms only.
        Orange and green are drawn by rebuildProfileWaveform from firmware data."""
        self.line_cp_cw.clear()
        self.line_cp_ccw.clear()

        if not self.cw_raw_harmonics and not self.ccw_raw_harmonics:
            return

        if self.cw_raw_harmonics:
            for deg in range(0, 361):
                theta = math.radians(deg)
                v = 0.0
                for order, amp, phase in self.cw_raw_harmonics:
                    v += amp * math.sin(order * theta + phase)
                self.line_cp_cw.append(float(deg), float(v))

        if self.ccw_raw_harmonics:
            for deg in range(0, 361):
                theta = math.radians(deg)
                v = 0.0
                for order, amp, phase in self.ccw_raw_harmonics:
                    v += amp * math.sin(order * theta + phase)
                self.line_cp_ccw.append(float(deg), float(v))



    def applyHarmonicMagnitudes(self):
        pass

    def updateProfilePosition(self):
        """Update the position dot and vertical marker on the profile chart.
        X = position (from MCU). Y = measured anti-cogging torque (from MCU)."""
        pos_deg = self.cogging_position * 360.0
        
        # Use the actual measured anti-cogging torque from MCU, not the computed harmonic value.
        torque = self.cogging_measured_torque
        
        # Update position dot
        self.scatter_cp_pos.clear()
        self.scatter_cp_pos.append(pos_deg, torque)
        
        # Update vertical marker line
        y_min = self.chart_cp_Yaxis.min()
        y_max = self.chart_cp_Yaxis.max()
        self.line_cp_vmarker.clear()
        self.line_cp_vmarker.append(pos_deg, y_min)
        self.line_cp_vmarker.append(pos_deg, y_max)

    def resetCoggingTable(self):
        self.send_value("tmc", "coggingTable", 0, instance=self.axis)
        self.clearCoggingGraph()

    def reloadCoggingTable(self):
        self.clearCoggingGraph()
        self.send_command("tmc", "coggingTable", self.axis, '?')
        self.send_command("tmc", "coggingHarmonics", self.axis, '?')
        self.send_command("tmc", "coggingCwCcw", self.axis, '?')

    def coggingScaleCb(self, val):
        pass  # magnitude slider removed; scale now read-only on TMC tab

    def openScaleCurveDialog(self):
        dlg = ScalePhaseAdvanceDialog(self, self.axis)
        dlg.exec()

    def coggingShapeCb(self, val):
        pass  # waveshape widget removed; shaping now handled by HarmShapingTab

    def getMotor(self):
        commands=["mtype","poles","encsrc","cpr","abnindex","abnpol","combineEncoder","invertForce","fluxbrake","calibrated"]
        self.send_commands("tmc",commands,self.axis)


    def getPids(self):
        commands = ["pidPrec","torqueP","torqueI","fluxP","fluxI","seqpi","svpwm"]
        self.send_commands("tmc",commands,self.axis)

        

    def setCurrentScaler(self,x):
        self.send_command("tmc","fluxoffset",self.axis)
        self.doubleSpinBox_fluxoffset.setEnabled(x > 0)
        self.doubleSpinBox_fluxoffset.setMaximum(round((0x7fff*x) / 3))
        if(x != self.adc_to_amps):
            self.adc_to_amps = x
            if(x > 0):
                self.chartYaxis_Amps.setMax(round((0x7fff*x) / 10))


class CurveEditorTab(QWidget):
    """One editable speed-dependent curve: a chart with a live RPM dot plus per-RPM spinboxes.

    Sliders
    - Left vertical:  first-point value (beginning of graph), maps 0–100 → y_min..y_max
    - Right vertical: last-point value (end of graph), same mapping
    - Knee horizontal: RPM breakpoint below which the curve is flattened to a plateau
      (snaps to actual RPM_POINTS values: 3,5,7,10,12,15…256)

    Shaping pipeline (non-compounding): base → remap between begin/end → knee flatten → clamp.
    Values are sent to the MCU automatically when a spinbox changes; the curve is
    fetched from the MCU on open.
    """
    # RPM breakpoints shared with firmware (must match scale_curve_rpm_defaults)
    RPM_POINTS = [0,5,7,10,12,15,20,25,30,35,40,50,60,70,80,90,100,120,140,160,180,200,225,256]

    def __init__(self, tmc_ui, axis, cmd_name, y_label, y_min, y_max, y_step, scale, decimals):
        """
        cmd_name : firmware command ('scaleCurve' or 'phaseAdvCurve')
        scale    : divisor applied to the integer value from/to the MCU to get the float value
        """
        super().__init__()
        self.tmc_ui = tmc_ui
        self.axis = axis
        self.cmd_name = cmd_name
        self.scale = float(scale)
        self._y_min = float(y_min)
        self._y_max = float(y_max)
        self._loading = False      # suppress handlers while programmatically updating spinboxes
        self._shaping_sync = False # guard against recursive slider cross-sync

        # Shaping state — all sliders derive visible spinbox values from these.
        self.base_values = [0.0] * len(self.RPM_POINTS)
        self.target_begin = 0.0   # desired value at first RPM (from left vertical slider)
        self.target_end = 0.0     # desired value at last RPM (from right vertical slider)
        self.knee_idx = 0         # breakpoint INDEX below which curve is flattened (0 = no flattening)
        self._slider_dragging = False  # true while a slider is being dragged
        self._send_debounce = QTimer(self)
        self._send_debounce.setSingleShot(True)
        self._send_debounce.setInterval(250)
        self._send_debounce.timeout.connect(self._debounced_send)

        layout = QVBoxLayout(self)

        # ---- Chart ----
        self.chart = QChart()
        self.chart.setMargins(QMargins(2,2,2,2))
        self.chart.legend().hide()
        self.axisX = QValueAxis()
        self.axisX.setTitleText("RPM")
        self.axisX.setRange(0, self.RPM_POINTS[-1])
        self.axisY = QValueAxis()
        self.axisY.setTitleText(y_label)
        self.axisY.setRange(y_min, y_max)
        self.chart.addAxis(self.axisX, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.axisY, Qt.AlignmentFlag.AlignLeft)

        self.curve_series = QLineSeries()
        self.curve_series.setColor(QColor("#3daee9"))
        self.chart.addSeries(self.curve_series)
        self.curve_series.attachAxis(self.axisX)
        self.curve_series.attachAxis(self.axisY)

        # Vertical knee marker (bright yellow so it's clearly visible when dragged)
        self.knee_series = QLineSeries()
        self.knee_series.setColor(QColor(255, 220, 0, 200))
        pen = self.knee_series.pen()
        pen.setWidth(2)
        self.knee_series.setPen(pen)
        self.knee_series.append(0, y_min)
        self.knee_series.append(0, y_max)
        self.chart.addSeries(self.knee_series)
        self.knee_series.attachAxis(self.axisX)
        self.knee_series.attachAxis(self.axisY)

        self.dot_series = QScatterSeries()
        self.dot_series.setColor(QColor("#da4453"))
        self.dot_series.setMarkerSize(10.0)
        self.chart.addSeries(self.dot_series)
        self.dot_series.attachAxis(self.axisX)
        self.dot_series.attachAxis(self.axisY)

        self.chartView = QChartView(self.chart)
        self.chartView.setMinimumHeight(220)

        # ---- Chart row: [begin-point slider] [chart] [end-point slider] ----
        chart_row = QHBoxLayout()

        # Left vertical — first RPM point value
        left_col = QVBoxLayout()
        left_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_col.addWidget(QLabel("Begin"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.slider_begin = QSlider(Qt.Orientation.Vertical)
        self.slider_begin.setRange(0, 100)
        self.slider_begin.setValue(50)
        self.slider_begin.setMinimumHeight(180)
        self.slider_begin.sliderPressed.connect(lambda: self._on_slider_press())
        self.slider_begin.valueChanged.connect(self._on_begin_slider)
        self.slider_begin.sliderReleased.connect(self._on_slider_release)
        left_col.addWidget(self.slider_begin, 1)
        self.spin_begin = QDoubleSpinBox()
        self.spin_begin.setRange(y_min, y_max)
        self.spin_begin.setDecimals(decimals)
        self.spin_begin.setSingleStep(y_step)
        self.spin_begin.setValue(0.0)
        self.spin_begin.valueChanged.connect(self._on_begin_spin)
        left_col.addWidget(self.spin_begin, 0)
        chart_row.addLayout(left_col)

        chart_row.addWidget(self.chartView, 1)

        # Right vertical — last RPM point value
        right_col = QVBoxLayout()
        right_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.addWidget(QLabel("End"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.slider_end = QSlider(Qt.Orientation.Vertical)
        self.slider_end.setRange(0, 100)
        self.slider_end.setValue(50)
        self.slider_end.setMinimumHeight(180)
        self.slider_end.sliderPressed.connect(lambda: self._on_slider_press())
        self.slider_end.valueChanged.connect(self._on_end_slider)
        self.slider_end.sliderReleased.connect(self._on_slider_release)
        right_col.addWidget(self.slider_end, 1)
        self.spin_end = QDoubleSpinBox()
        self.spin_end.setRange(y_min, y_max)
        self.spin_end.setDecimals(decimals)
        self.spin_end.setSingleStep(y_step)
        self.spin_end.setValue(0.0)
        self.spin_end.valueChanged.connect(self._on_end_spin)
        right_col.addWidget(self.spin_end, 0)
        chart_row.addLayout(right_col)
        layout.addLayout(chart_row)

        # ---- Knee RPM slider (horizontal, snaps to actual RPM breakpoints) ----
        knee_row = QHBoxLayout()
        knee_row.addWidget(QLabel("Knee RPM:"))
        self.slider_knee = QSlider(Qt.Orientation.Horizontal)
        self.slider_knee.setRange(0, len(self.RPM_POINTS) - 1)
        self.slider_knee.setValue(0)
        self.slider_knee.sliderPressed.connect(lambda: self._on_slider_press())
        self.slider_knee.valueChanged.connect(self._on_knee_slider)
        self.slider_knee.sliderReleased.connect(self._on_slider_release)
        knee_row.addWidget(self.slider_knee, 1)
        self.spin_knee = QSpinBox()
        self.spin_knee.setRange(0, self.RPM_POINTS[-1])
        self.spin_knee.setValue(0)
        self.spin_knee.setSuffix(" RPM")
        self.spin_knee.valueChanged.connect(self._on_knee_spin)
        knee_row.addWidget(self.spin_knee)
        layout.addLayout(knee_row)

        # Status line
        self.lbl_status = QLabel("Begin: --   End: --   Knee: 0 RPM")
        layout.addWidget(self.lbl_status)

        # ---- Spinboxes grid in scroll area ----
        spin_container = QWidget()
        grid = QGridLayout(spin_container)
        grid.setContentsMargins(0, 0, 0, 0)
        self.spinboxes = []
        cols = 4
        for i, rpm in enumerate(self.RPM_POINTS):
            sb = QDoubleSpinBox()
            sb.setRange(y_min, y_max)
            sb.setDecimals(decimals)
            sb.setSingleStep(y_step)
            sb.setValue(0.0)
            sb.valueChanged.connect(lambda val, idx=i: self._on_spin_changed(idx, val))
            grid.addWidget(QLabel(f"{rpm} RPM:"), i // cols, (i % cols) * 2)
            grid.addWidget(sb, i // cols, (i % cols) * 2 + 1)
            self.spinboxes.append(sb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(spin_container)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll)

        self.redraw_curve()

    # ---- Slider drag / debounce ----
    def _on_slider_press(self):
        self._slider_dragging = True
        self._send_debounce.stop()

    def _on_slider_release(self):
        self._slider_dragging = False
        self._send_debounce.stop()
        self._apply_shaping(send=True)

    def _debounced_send(self):
        """Fire after 250ms quiet — sends if slider is still at rest (no drag)."""
        if not self._slider_dragging:
            self._apply_shaping(send=True)

    # ---- Slider helpers ----
    def _slider_to_value(self, slider_val):
        """Map 0–100 slider integer → y_min..y_max."""
        return self._y_min + (slider_val / 100.0) * (self._y_max - self._y_min)

    def _value_to_slider(self, val):
        """Map y_min..y_max value → 0–100 slider integer."""
        span = self._y_max - self._y_min
        if span <= 0:
            return 0
        return int(round((val - self._y_min) / span * 100.0))

    # ---- Slider callbacks ----
    def _on_begin_slider(self, val):
        """Target value for the first RPM point. Whole curve remaps between begin–end."""
        if self._shaping_sync:
            return
        self.target_begin = self._slider_to_value(val)
        self._apply_shaping(send=False)
        if not self._slider_dragging:
            self._send_debounce.start()

    def _on_end_slider(self, val):
        """Target value for the last RPM point. Whole curve remaps between begin–end."""
        if self._shaping_sync:
            return
        self.target_end = self._slider_to_value(val)
        self._apply_shaping(send=False)
        if not self._slider_dragging:
            self._send_debounce.start()

    def _on_begin_spin(self, val):
        """Begin spinbox edited: snap slider and apply."""
        if self._shaping_sync:
            return
        self.target_begin = float(val)
        self._apply_shaping(send=True)

    def _on_end_spin(self, val):
        """End spinbox edited: snap slider and apply."""
        if self._shaping_sync:
            return
        self.target_end = float(val)
        self._apply_shaping(send=True)

    def _on_knee_slider(self, val):
        """Knee slider position == breakpoint index. 0 = no flattening."""
        if self._shaping_sync:
            return
        self.knee_idx = int(val)
        self._apply_shaping(send=False)
        if not self._slider_dragging:
            self._send_debounce.start()

    def _on_knee_spin(self, val):
        """Knee RPM typed: find nearest breakpoint index and snap slider."""
        if self._shaping_sync:
            return
        # Find the breakpoint index whose RPM is closest to typed value
        rpm_val = int(val)
        best_idx = 0
        best_dist = abs(rpm_val - 0)
        for i, r in enumerate(self.RPM_POINTS):
            d = abs(rpm_val - r)
            if d < best_dist:
                best_dist = d
                best_idx = i
        self.knee_idx = best_idx
        self._apply_shaping(send=True)

    # ---- Shaping pipeline ----
    def _apply_shaping(self, send=False):
        """Recompute visible spinbox values as a plateau + linear ramp defined by the sliders.

        Model (sliders fully define the shape; base curve shape is not preserved):
          - Points with index < knee_idx: flat at target_begin
          - Points with index >= knee_idx: linear ramp from target_begin to target_end
        Clamp to Y range.  If send=True, push all points to the MCU.
        """
        if not self.base_values:
            return

        N = len(self.RPM_POINTS)
        knee_idx = max(0, min(self.knee_idx, N - 1))
        rpm_knee = self.RPM_POINTS[knee_idx]
        rpm_last = self.RPM_POINTS[-1]
        rpm_span = rpm_last - rpm_knee  # RPM range covered by the ramp region

        self._loading = True
        for i, sb in enumerate(self.spinboxes):
            rpm_i = self.RPM_POINTS[i]
            if i < knee_idx:
                v = self.target_begin                       # plateau below knee
            else:
                if rpm_span <= 0:
                    # knee at the very last point: only that point takes target_end
                    v = self.target_end if i == N - 1 else self.target_begin
                else:
                    # Linear interpolation based on actual RPM, not point index
                    t = (rpm_i - rpm_knee) / rpm_span       # 0 at knee RPM → 1 at last RPM
                    v = self.target_begin + t * (self.target_end - self.target_begin)
            v = max(self._y_min, min(self._y_max, v))
            sb.setValue(v)
        self._loading = False

        if send:
            for i, sb in enumerate(self.spinboxes):
                self.tmc_ui.send_value("tmc", self.cmd_name, adr=i,
                                       val=int(round(sb.value() * self.scale)), instance=self.axis)

        self._sync_vertical_sliders()
        self._update_knee_marker()
        self.redraw_curve()
        self._update_status()

    def _sync_vertical_sliders(self):
        """Push target_begin/target_end/knee_idx back to slider positions and spinboxes."""
        self._shaping_sync = True
        self.slider_begin.blockSignals(True)
        self.slider_end.blockSignals(True)
        self.slider_knee.blockSignals(True)
        self.spin_begin.blockSignals(True)
        self.spin_end.blockSignals(True)
        self.spin_knee.blockSignals(True)
        self.slider_begin.setValue(self._value_to_slider(self.target_begin))
        self.slider_end.setValue(self._value_to_slider(self.target_end))
        self.slider_knee.setValue(self.knee_idx)
        self.spin_begin.setValue(self.target_begin)
        self.spin_end.setValue(self.target_end)
        self.spin_knee.setValue(self.RPM_POINTS[self.knee_idx])
        self.slider_begin.blockSignals(False)
        self.slider_end.blockSignals(False)
        self.slider_knee.blockSignals(False)
        self.spin_begin.blockSignals(False)
        self.spin_end.blockSignals(False)
        self.spin_knee.blockSignals(False)
        self._shaping_sync = False

    def _update_knee_marker(self):
        self.knee_series.clear()
        knee_rpm = self.RPM_POINTS[self.knee_idx]
        self.knee_series.append(knee_rpm, self._y_min)
        self.knee_series.append(knee_rpm, self._y_max)

    def _update_status(self):
        begin_txt = f"{self.target_begin:.2f}" if self.base_values else "--"
        end_txt = f"{self.target_end:.2f}" if self.base_values else "--"
        knee_rpm = self.RPM_POINTS[self.knee_idx]
        knee_txt = f"{knee_rpm}" if self.knee_idx > 0 else "none"
        self.lbl_status.setText(
            f"Begin: {begin_txt}   End: {end_txt}   Knee: {knee_txt} RPM")

    def _reset_shaping_sliders(self):
        """Return all sliders and spinboxes to neutral without firing handlers."""
        self._shaping_sync = True
        for s in (self.slider_begin, self.slider_end, self.slider_knee,
                  self.spin_begin, self.spin_end, self.spin_knee):
            s.blockSignals(True)
        self.slider_begin.setValue(self._value_to_slider(self.target_begin))
        self.slider_end.setValue(self._value_to_slider(self.target_end))
        self.slider_knee.setValue(self.knee_idx)
        self.spin_begin.setValue(self.target_begin)
        self.spin_end.setValue(self.target_end)
        self.spin_knee.setValue(self.RPM_POINTS[self.knee_idx])
        for s in (self.slider_begin, self.slider_end, self.slider_knee,
                  self.spin_begin, self.spin_end, self.spin_knee):
            s.blockSignals(False)
        self._shaping_sync = False

    # ---- Data I/O ----
    def set_values(self, float_values):
        """Populate spinboxes from a list of float values (len == RPM_POINTS) without sending.

        Snapshots this as the new shaping base and sets targets to match actual endpoints.
        """
        self._loading = True
        for i, sb in enumerate(self.spinboxes):
            if i < len(float_values):
                sb.setValue(float(float_values[i]))
        self._loading = False
        self.base_values = [sb.value() for sb in self.spinboxes]
        # Targets match the loaded curve endpoints (neutral shaping)
        self.target_begin = self.base_values[0] if self.base_values else 0.0
        self.target_end = self.base_values[-1] if self.base_values else 0.0
        self.knee_idx = 0
        self._reset_shaping_sliders()
        self._update_knee_marker()
        self.redraw_curve()
        self._update_status()

    def _on_spin_changed(self, idx, val):
        if self._loading:
            return
        # A manual spinbox edit is authoritative: re-snapshot base and neutralize shaping.
        self.base_values = [sb.value() for sb in self.spinboxes]
        self.target_begin = self.base_values[0] if self.base_values else 0.0
        self.target_end = self.base_values[-1] if self.base_values else 0.0
        self.knee_idx = 0
        self._reset_shaping_sliders()
        self._update_knee_marker()
        # Auto-send the edited point to MCU (integer encoded)
        self.tmc_ui.send_value("tmc", self.cmd_name, adr=idx,
                               val=int(round(val * self.scale)), instance=self.axis)
        self.redraw_curve()
        self._update_status()

    def redraw_curve(self):
        self.curve_series.clear()
        for i, sb in enumerate(self.spinboxes):
            self.curve_series.append(self.RPM_POINTS[i], sb.value())

    def set_live_rpm(self, rpm):
        val = self.interpolate(rpm)
        self.dot_series.clear()
        self.dot_series.append(rpm, val)

    def interpolate(self, rpm):
        pts = self.RPM_POINTS
        vals = [sb.value() for sb in self.spinboxes]
        if rpm <= pts[0]:
            return vals[0]
        for i in range(len(pts) - 1):
            if pts[i] <= rpm <= pts[i + 1]:
                span = pts[i + 1] - pts[i]
                if span <= 0:
                    return vals[i]
                t = (rpm - pts[i]) / span
                return vals[i] + t * (vals[i + 1] - vals[i])
        return vals[-1]


class HarmShapingTab(QWidget):
    """Editor for the cogging waveshaping ("3rd harmonic") parameters.

    Lets the user subtract/add a harmonic of the DOMINANT detected cogging order
    to reshape the compensation profile (thin peaks / steep slopes) so it matches
    the physical stator-tooth feel better than the raw Fourier sum.

    Firmware command `coggingH3` (setat): adr 0 = shaping(*1000), 1 = phase trim
    (mrad), 2 = mult (1..31). get returns "shaping:phaseTrim:mult".

    The chart previews one revolution of the original cogging compensation (from
    the cached harmonic table) versus the shaped wave, so the effect is visible.
    """

    def __init__(self, tmc_ui, axis):
        super().__init__()
        self.tmc_ui = tmc_ui
        self.axis = axis
        self._loading = False

        layout = QVBoxLayout(self)

        # ---- Chart ----
        self.chart = QChart()
        self.chart.setMargins(QMargins(2, 2, 2, 2))
        self.chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.axisX = QValueAxis()
        self.axisX.setTitleText("Angle (deg)")
        self.axisX.setRange(0, 360)
        self.axisY = QValueAxis()
        self.axisY.setTitleText("Compensation")
        self.chart.addAxis(self.axisX, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.axisY, Qt.AlignmentFlag.AlignLeft)

        self.orig_series = QLineSeries()
        self.orig_series.setName("Original")
        self.orig_series.setColor(QColor("#3daee9"))
        self.chart.addSeries(self.orig_series)
        self.orig_series.attachAxis(self.axisX)
        self.orig_series.attachAxis(self.axisY)

        self.shaped_series = QLineSeries()
        self.shaped_series.setName("Shaped")
        self.shaped_series.setColor(QColor("#da4453"))
        self.chart.addSeries(self.shaped_series)
        self.shaped_series.attachAxis(self.axisX)
        self.shaped_series.attachAxis(self.axisY)

        self.chartView = QChartView(self.chart)
        self.chartView.setMinimumHeight(240)
        layout.addWidget(self.chartView, 1)

        # ---- Controls ----
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Shaping factor (slider + spinbox): signed fraction of dominant amplitude.
        self.slider_shaping = QSlider(Qt.Orientation.Horizontal)
        self.slider_shaping.setRange(-100, 100)  # -1.00 .. +1.00 in steps of 0.01
        self.slider_shaping.setValue(0)
        self.slider_shaping.valueChanged.connect(self._on_shaping_slider)
        shaping_row = QHBoxLayout()
        shaping_row.addWidget(self.slider_shaping, 1)
        self.spin_shaping = QDoubleSpinBox()
        self.spin_shaping.setRange(-1.0, 1.0)
        self.spin_shaping.setSingleStep(0.01)
        self.spin_shaping.setDecimals(3)
        self.spin_shaping.setSuffix("")
        self.spin_shaping.valueChanged.connect(self._on_shaping_spin)
        shaping_row.addWidget(self.spin_shaping)
        shaping_w = QWidget()
        shaping_w.setLayout(shaping_row)
        form.addRow("Shaping (+ = subtract, thin peaks):", shaping_w)

        # Harmonic multiplier of the dominant order (2,3,5...).
        self.spin_mult = QSpinBox()
        self.spin_mult.setRange(1, 31)
        self.spin_mult.setValue(3)
        self.spin_mult.valueChanged.connect(self._on_mult_changed)
        form.addRow("Harmonic multiplier (3 = 3rd):", self.spin_mult)

        # Phase trim in degrees — rotates the applied shaping harmonic around the
        # electrical angle. This shifts where along the revolution the peak‑shaving
        # effect lands, letting you align it precisely with the physical detent position.
        self.spin_phase = QDoubleSpinBox()
        self.spin_phase.setRange(-180.0, 180.0)
        self.spin_phase.setSingleStep(1.0)
        self.spin_phase.setDecimals(2)
        self.spin_phase.setSuffix(" deg")
        self.spin_phase.setValue(0.0)
        self.spin_phase.setToolTip("Rotates the shaped harmonic around the electrical revolution "
                                   "so the peak‑shaving effect aligns with the physical detents")
        self.spin_phase.valueChanged.connect(self._on_phase_changed)
        form.addRow("Phase trim:", self.spin_phase)

        # Read-only info about the detected dominant harmonic.
        self.label_dom = QLabel("Dominant order: —")
        form.addRow("", self.label_dom)

        layout.addLayout(form)

        self._load_from_mcu()

    # ---------- loading ----------
    def _load_from_mcu(self):
        self.tmc_ui.get_value_async("tmc", "coggingH3", self._h3_cb, self.axis, str)

    def _h3_cb(self, data):
        try:
            parts = str(data).split(":")
            if len(parts) >= 3:
                shaping = float(int(parts[0])) / 1000.0
                phase_mrad = float(int(parts[1]))
                mult = int(parts[2])
                self._loading = True
                self.spin_shaping.setValue(shaping)
                self.spin_phase.setValue(phase_mrad / 1000.0 * 180.0 / math.pi)
                if 1 <= mult <= 31:
                    self.spin_mult.setValue(mult)
                self.slider_shaping.setValue(int(round(shaping * 100)))
                self._loading = False
        except Exception:
            self._loading = False
        self.redraw()

    # ---------- handlers ----------
    def _on_shaping_slider(self, val):
        if self._loading:
            return
        v = val / 100.0
        qtBlockAndCall(self.spin_shaping, self.spin_shaping.setValue, v)
        self._send(0, int(round(v * 1000.0)))
        self.redraw()

    def _on_shaping_spin(self, val):
        if self._loading:
            return
        qtBlockAndCall(self.slider_shaping, self.slider_shaping.setValue, int(round(val * 100)))
        self._send(0, int(round(val * 1000.0)))
        self.redraw()

    def _on_mult_changed(self, val):
        if self._loading:
            return
        self._send(2, int(val))
        self.redraw()

    def _on_phase_changed(self, val):
        if self._loading:
            return
        # degrees -> millirad
        mrad = int(round(val * math.pi / 180.0 * 1000.0))
        self._send(1, mrad)
        self.redraw()

    def _send(self, adr, val):
        self.tmc_ui.send_value("tmc", "coggingH3", val=val, adr=adr, instance=self.axis)

    # ---------- preview ----------
    def _dominant_harmonic(self):
        """Return (order, amp, phase_rad) of the largest cached cogging harmonic, or None."""
        harms = getattr(self.tmc_ui, "cogging_harmonics_data", [])
        best = None
        for h in harms:
            try:
                order, amp, phase_rad = int(h[0]), float(h[1]), float(h[2])
            except Exception:
                continue
            if amp > 0 and (best is None or amp > best[1]):
                best = (order, amp, phase_rad)
        return best

    def redraw(self):
        dom = self._dominant_harmonic()
        mult = self.spin_mult.value()
        if dom is not None:
            actual_order = dom[0] * mult
            self.label_dom.setText(f"Dominant order: {dom[0]}  →  editing order {actual_order}  (amp {dom[1]:.0f})")
        else:
            self.label_dom.setText("Dominant order: —  (no harmonic table cached)")

        harms = getattr(self.tmc_ui, "cogging_harmonics_data", [])
        shaping = self.spin_shaping.value()
        mult = self.spin_mult.value()
        phase_trim_rad = self.spin_phase.value() * math.pi / 180.0

        # Reconstruct original and shaped waves over one revolution.
        N = 360
        orig = [0.0] * N
        shaped = [0.0] * N
        have_data = False
        for h in harms:
            try:
                order, amp, phase_rad = int(h[0]), float(h[1]), float(h[2])
            except Exception:
                continue
            if amp <= 0:
                continue
            have_data = True
            for i in range(N):
                theta = (i / N) * 2.0 * math.pi
                orig[i] += amp * math.sin(order * theta + phase_rad)

        if not have_data:
            self.orig_series.clear()
            self.shaped_series.clear()
            return

        # Apply shaping term using the dominant harmonic.
        for i in range(N):
            theta = (i / N) * 2.0 * math.pi
            v = orig[i]
            if dom is not None and shaping != 0.0:
                d_order, d_amp, d_phase_rad = dom
                shaped_arg = mult * (d_order * theta + d_phase_rad) + phase_trim_rad
                v -= shaping * d_amp * math.sin(shaped_arg)
            shaped[i] = v

        self.orig_series.clear()
        self.shaped_series.clear()
        y_min = min(min(orig), min(shaped))
        y_max = max(max(orig), max(shaped))
        if y_max - y_min < 1e-6:
            y_max = y_min + 1.0
        pad = (y_max - y_min) * 0.1
        self.axisY.setRange(y_min - pad, y_max + pad)
        for i in range(N):
            deg = i  # 0..359
            self.orig_series.append(deg, orig[i])
            self.shaped_series.append(deg, shaped[i])


class ScalePhaseAdvanceDialog(QDialog):
    """Tabbed editor for the speed-dependent Scale Curve and Phase Advance curve.

    Both curves auto-load from the MCU on open. A live red dot tracks the current
    RPM and the interpolated value on each chart. Spinbox edits are pushed to the
    MCU immediately (no manual send button required).
    """
    def __init__(self, tmc_ui, axis):
        super().__init__(tmc_ui)
        self.tmc_ui = tmc_ui
        self.axis = axis
        self.setWindowTitle("Manual Tuning - Scale & Phase Advance Curves")
        self.setMinimumSize(720, 560)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Scale Curve tab: value range 0..10, MCU encodes *1000
        self.scale_tab = CurveEditorTab(
            tmc_ui, axis, "scaleCurve", "Scale", 0.0, 10.0, 0.1, scale=1000.0, decimals=2)
        self.tabs.addTab(self.scale_tab, "Scale Curve")

        # Phase Advance tab: value in degrees, MCU encodes *100
        self.phase_tab = CurveEditorTab(
            tmc_ui, axis, "phaseAdvCurve", "Phase Advance (deg)", -5.0, 15.0, 0.25, scale=100.0, decimals=2)
        self.tabs.addTab(self.phase_tab, "Phase Advance")

        # 3rd-harmonic waveshaping tab: reshapes the cogging compensation profile.
        self.h3_tab = HarmShapingTab(tmc_ui, axis)
        self.tabs.addTab(self.h3_tab, "Harmonic Editor")

        # Live RPM dot polling
        self.live_timer = QTimer(self)
        self.live_timer.timeout.connect(self._poll_live)
        self.current_rpm = 0.0

        # Auto-load both curves from MCU on open
        self._load_curves()

    def showEvent(self, event):
        super().showEvent(event)
        self._load_curves()  # re-fetch from MCU every time dialog opens
        self.live_timer.start(50)

    def hideEvent(self, event):
        self.live_timer.stop()
        super().hideEvent(event)

    def _load_curves(self):
        # Register callbacks with typechar='?' to match firmware reply format.
        # Firmware replies: "[tmc.0.scaleCurve?|3:1000,5:1050,...]"
        self.tmc_ui.register_callback("tmc", "scaleCurve", self._scale_curve_cb, self.axis, str, typechar='?', delete=True)
        self.tmc_ui.register_callback("tmc", "phaseAdvCurve", self._phase_curve_cb, self.axis, str, typechar='?', delete=True)
        self.tmc_ui.send_command("tmc", "scaleCurve", self.axis, '?')
        self.tmc_ui.send_command("tmc", "phaseAdvCurve", self.axis, '?')
        # Request the harmonic table so the 3rd-harmonic preview can render.
        # The reply is cached on the main UI (updateCoggingHarmonics); we redraw shortly after.
        self.tmc_ui.send_command("tmc", "coggingHarmonics", self.axis, '?')
        QTimer.singleShot(300, self.h3_tab.redraw)

    def _parse_curve(self, data, scale):
        """Parse 'rpm:int,rpm:int,...' into a list of floats ordered by CurveEditorTab.RPM_POINTS."""
        result = [0.0] * len(CurveEditorTab.RPM_POINTS)
        try:
            for item in str(data).split(","):
                parts = item.split(":")
                if len(parts) >= 2:
                    r = int(parts[0])
                    v = float(parts[1]) / scale
                    if r in CurveEditorTab.RPM_POINTS:
                        idx = CurveEditorTab.RPM_POINTS.index(r)
                        result[idx] = v
        except Exception:
            pass
        return result

    def _scale_curve_cb(self, data):
        vals = self._parse_curve(data, 1000.0)
        self.scale_tab.set_values(vals)

    def _phase_curve_cb(self, data):
        vals = self._parse_curve(data, 100.0)
        self.phase_tab.set_values(vals)

    def _poll_live(self):
        """Read cached RPM from the main tab (updated every 50ms by its own timer)."""
        self.current_rpm = abs(getattr(self.tmc_ui, 'vel_rpm', 0.0))
        self.scale_tab.set_live_rpm(self.current_rpm)
        self.phase_tab.set_live_rpm(self.current_rpm)


class TMC_HW_Version_Selector(OptionsDialogGroupBox,CommunicationHandler):

    def __init__(self,name,parent : TMC4671Ui,instance):
        self.parent = parent
        OptionsDialogGroupBox.__init__(self,name,parent)
        CommunicationHandler.__init__(self)
        self.typeBox = QGroupBox("Hardware Version")
        self.typeBoxLayout = QVBoxLayout()
        self.typeBox.setLayout(self.typeBoxLayout)
        self.axis = instance

    def initUI(self):
        vbox = QVBoxLayout()
        self.infolabel = QLabel(self.tr("Warning: Selecting the incorrect hardware version can lead to damage to the hardware or injury.\nSeveral calibration constants and safety features depend on the correct selection."))
        vbox.addWidget(self.infolabel)
        self.combobox = QComboBox()
        vbox.addWidget(self.combobox)
        self.setLayout(vbox)

    def onclose(self):
        self.remove_callbacks()


    def apply(self):
        self.send_value("tmc","tmcHwType",self.combobox.currentData(),instance=self.axis) # current data
        self.parent.init_ui() # Update TMC UI in case capabilities have changed
    
    def typeCb(self,entries):
        #print("Reply",entries)
        entriesList = entries.split("\n")
        entriesList = [m.split(":") for m in entriesList if m]
        for m in entriesList:
            self.combobox.addItem(m[1],m[0])
        self.get_value_async("tmc","tmcHwType",lambda val : self.combobox.setCurrentIndex(self.combobox.findData(val)),self.axis,int)

    def readValues(self):
        self.get_value_async("tmc","tmcHwType",self.typeCb,self.axis,str,typechar='!')

