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
        self.max_datapoints = 10000
        self.max_datapointsVisibleTime = 30
        self.adc_to_amps = 0#2.5 / (0x7fff * 60.0 * 0.0015)

        self.hwversion = 0
        self.hwversions = []
        self.versionWarningShow = True
        self.vext = 0
        self.vint = 0

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
        self.doubleSpinBox_coggScale.setMinimum(-3.2767)
        self.doubleSpinBox_coggScale.setMaximum(3.2767)
        self.doubleSpinBox_coggScale.setSingleStep(0.001)
        self.doubleSpinBox_coggScale.setDecimals(4)
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
        self.chart_cp_Xaxis.setTitleText("Position (deg)")
        self.chart_cp_Xaxis.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),128))
        self.chart_cogging_profile.addAxis(self.chart_cp_Xaxis, Qt.AlignmentFlag.AlignBottom)

        self.chart_cp_Yaxis = QValueAxis(self.chart_cogging_profile)
        self.chart_cp_Yaxis.setTitleText("Torque")
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
            self.cogging_scale = int(tflist[3]) / 100.0  # scale*100 from MCU
            self.horizontalSlider_coggmag.setValue(int(self.cogging_scale * 10000.0))
        if len(tflist) >= 5:
            pos = tflist[4] / 10000.0  # normalized 0-1
            self.cogging_position = pos
            self.updateProfilePosition()
        vel_rpm = 0
        if len(tflist) >= 6:
            vel_rpm = int(tflist[5])  # velocity RPM from MCU
            
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
        self.send_value("tmc","coggingScale",val=self.horizontalSlider_coggmag.value(),instance=self.axis)
        self.send_value("tmc","coggingShape",val=int(round(self.spinBox_waveshape.value() * 100.0)),instance=self.axis)
        
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
                self.horizontalSlider_coggmag.valueChanged.connect(self.coggingScaleChanged)
                self.doubleSpinBox_coggScale.valueChanged.connect(self.coggingSpinBoxChanged)
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

    def coggingScaleChanged(self, val):
        qtBlockAndCall(self.doubleSpinBox_coggScale, self.doubleSpinBox_coggScale.setValue, val / 10000.0)
        self.send_value("tmc", "coggingScale", val=val, instance=self.axis)

    def coggingSpinBoxChanged(self, val):
        slider_val = int(round(val * 10000.0))
        qtBlockAndCall(self.horizontalSlider_coggmag, self.horizontalSlider_coggmag.setValue, slider_val)
        self.send_value("tmc", "coggingScale", val=slider_val, instance=self.axis)

    def coggingScaleCb(self, val):
        qtBlockAndCall(self.horizontalSlider_coggmag, self.horizontalSlider_coggmag.setValue, val)
        qtBlockAndCall(self.doubleSpinBox_coggScale, self.doubleSpinBox_coggScale.setValue, val / 10000.0)

    def openScaleCurveDialog(self):
        dlg = ScalePhaseAdvanceDialog(self, self.axis)
        dlg.exec()

    def waveshapeChanged(self, val):
        self.send_value("tmc", "coggingShape", val=int(round(val * 100.0)), instance=self.axis)

    def coggingShapeCb(self, val):
        qtBlockAndCall(self.spinBox_waveshape, self.spinBox_waveshape.setValue, val / 100.0)

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

    Shared by the Scale Curve and Phase Advance tabs. Values are sent to the MCU
    automatically whenever a spinbox changes; the curve is fetched from the MCU on open.
    """
    # RPM breakpoints shared with firmware (must match scale_curve_rpm_defaults)
    RPM_POINTS = [3,5,7,10,12,15,20,25,30,35,40,50,60,70,80,90,100,120,140,160,180,200,225,256]

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
        self._loading = False    # suppress handlers while programmatically updating spinboxes
        self._slider_syncing = False  # guard against recursive slider cross-sync

        # Shaping state. Both the scale sliders and the knee slider re-derive the
        # visible spinbox values from `base_values`, so dragging never compounds.
        self.base_values = [0.0] * len(self.RPM_POINTS)
        self.current_mult = 1.0   # global vertical multiplier applied to all points
        self.knee_rpm = 0         # RPM breakpoint below which the curve is flattened

        layout = QVBoxLayout(self)

        # Chart
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

        # Vertical marker showing the current knee RPM
        self.knee_series = QLineSeries()
        self.knee_series.setColor(QColor(255, 255, 255, 90))
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

        # Chart row: [scale-down slider] [chart] [scale-up slider]
        chart_row = QHBoxLayout()
        # Left vertical slider (beginning of graph): shrink whole curve toward 0 (0–100%)
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("Scale ▼"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.slider_left = QSlider(Qt.Orientation.Vertical)
        self.slider_left.setRange(0, 100)
        self.slider_left.setValue(100)
        self.slider_left.setMinimumHeight(200)
        self.slider_left.valueChanged.connect(self._on_left_slider)
        self.slider_left.sliderReleased.connect(lambda: self._apply_shaping(send=True))
        left_col.addWidget(self.slider_left, 1)
        left_col.addWidget(QLabel("0%"), 0, Qt.AlignmentFlag.AlignHCenter)
        chart_row.addLayout(left_col)

        chart_row.addWidget(self.chartView, 1)

        # Right vertical slider (end of graph): amplify whole curve (100–300%)
        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("Scale ▲"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.slider_right = QSlider(Qt.Orientation.Vertical)
        self.slider_right.setRange(100, 300)
        self.slider_right.setValue(100)
        self.slider_right.setMinimumHeight(200)
        self.slider_right.valueChanged.connect(self._on_right_slider)
        self.slider_right.sliderReleased.connect(lambda: self._apply_shaping(send=True))
        right_col.addWidget(self.slider_right, 1)
        right_col.addWidget(QLabel("300%"), 0, Qt.AlignmentFlag.AlignHCenter)
        chart_row.addLayout(right_col)
        layout.addLayout(chart_row)

        # Horizontal knee slider: start-RPM breakpoint that flattens the low-RPM plateau
        knee_row = QHBoxLayout()
        knee_row.addWidget(QLabel("Knee RPM:"))
        self.slider_knee = QSlider(Qt.Orientation.Horizontal)
        self.slider_knee.setRange(0, int(self.RPM_POINTS[-1]))
        self.slider_knee.setValue(0)
        self.slider_knee.valueChanged.connect(self._on_knee_slider)
        self.slider_knee.sliderReleased.connect(lambda: self._apply_shaping(send=True))
        knee_row.addWidget(self.slider_knee, 1)
        self.lbl_knee = QLabel("0 (none)")
        knee_row.addWidget(self.lbl_knee)
        layout.addLayout(knee_row)

        self.lbl_status = QLabel("Scale: 100%   |   Knee: 0 RPM")
        layout.addWidget(self.lbl_status)

        # Spinboxes in a grid (label + value), wrapped into a scroll area
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

        # Redraw the static curve from the spinbox values
        self.redraw_curve()

    # ---- Shaping (sliders) ----
    def _on_left_slider(self, val):
        """Scale-DOWN slider (0–100% => mult 0.0–1.0). Snaps the up-slider to neutral."""
        if self._slider_syncing:
            return
        self._slider_syncing = True
        self.slider_right.blockSignals(True)
        self.slider_right.setValue(100)
        self.slider_right.blockSignals(False)
        self._slider_syncing = False
        self.current_mult = val / 100.0
        self._apply_shaping(send=False)

    def _on_right_slider(self, val):
        """Scale-UP slider (100–300% => mult 1.0–3.0). Snaps the down-slider to neutral."""
        if self._slider_syncing:
            return
        self._slider_syncing = True
        self.slider_left.blockSignals(True)
        self.slider_left.setValue(100)
        self.slider_left.blockSignals(False)
        self._slider_syncing = False
        self.current_mult = val / 100.0
        self._apply_shaping(send=False)

    def _on_knee_slider(self, val):
        """Knee RPM: flatten all breakpoints below this RPM into a plateau."""
        self.knee_rpm = float(val)
        self._apply_shaping(send=False)

    def _apply_shaping(self, send=False):
        """Recompute visible spinbox values from base_values with knee + global scale.

        Pipeline (non-compounding): start from base -> flatten low-RPM plateau to the
        value at the knee breakpoint -> multiply by current_mult -> clamp to Y range.
        If send=True, push all points to the MCU.
        """
        if not self.base_values:
            return
        # First breakpoint at/above the knee RPM defines the plateau value.
        knee_idx = len(self.RPM_POINTS) - 1
        for i, r in enumerate(self.RPM_POINTS):
            if r >= self.knee_rpm:
                knee_idx = i
                break
        plateau = self.base_values[knee_idx]

        self._loading = True
        for i, sb in enumerate(self.spinboxes):
            v = self.base_values[i]
            if i < knee_idx:
                v = plateau
            v *= self.current_mult
            v = max(self._y_min, min(self._y_max, v))
            sb.setValue(v)
        self._loading = False

        if send:
            for i, sb in enumerate(self.spinboxes):
                self.tmc_ui.send_value("tmc", self.cmd_name, adr=i,
                                       val=int(round(sb.value() * self.scale)), instance=self.axis)

        self._update_knee_marker()
        self.redraw_curve()
        self._update_status()

    def _update_knee_marker(self):
        """Draw/refresh the vertical knee marker line at the current knee RPM."""
        self.knee_series.clear()
        self.knee_series.append(self.knee_rpm, self._y_min)
        self.knee_series.append(self.knee_rpm, self._y_max)

    def _update_status(self):
        pct = int(round(self.current_mult * 100.0))
        knee_txt = f"{int(round(self.knee_rpm))}" if self.knee_rpm > 0 else "0 (none)"
        self.lbl_status.setText(f"Scale: {pct}%   |   Knee: {knee_txt} RPM")
        self.lbl_knee.setText(knee_txt)

    def _reset_shaping_sliders(self):
        """Return all three sliders to neutral without firing handlers."""
        self._slider_syncing = True
        for s in (self.slider_left, self.slider_right, self.slider_knee):
            s.blockSignals(True)
        self.slider_left.setValue(100)
        self.slider_right.setValue(100)
        self.slider_knee.setValue(0)
        for s in (self.slider_left, self.slider_right, self.slider_knee):
            s.blockSignals(False)
        self._slider_syncing = False

    def set_values(self, float_values):
        """Populate spinboxes from a list of float values (len == RPM_POINTS) without sending.

        Also takes this as the new shaping base and neutralizes any active shaping.
        """
        self._loading = True
        for i, sb in enumerate(self.spinboxes):
            if i < len(float_values):
                sb.setValue(float(float_values[i]))
        self._loading = False
        self.base_values = [sb.value() for sb in self.spinboxes]
        self.current_mult = 1.0
        self.knee_rpm = 0.0
        self._reset_shaping_sliders()
        self._update_knee_marker()
        self.redraw_curve()
        self._update_status()

    def _on_spin_changed(self, idx, val):
        if self._loading:
            return
        # A manual spinbox edit is authoritative: snapshot the whole visible curve
        # as the new base and neutralize shaping so it cannot compound afterwards.
        self.base_values = [sb.value() for sb in self.spinboxes]
        self.current_mult = 1.0
        self.knee_rpm = 0.0
        self._reset_shaping_sliders()
        self._update_knee_marker()
        # Auto-send the edited point to MCU (integer encoded)
        self.tmc_ui.send_value("tmc", self.cmd_name, adr=idx, val=int(round(val * self.scale)), instance=self.axis)
        self.redraw_curve()
        self._update_status()

    def redraw_curve(self):
        self.curve_series.clear()
        for i, sb in enumerate(self.spinboxes):
            self.curve_series.append(self.RPM_POINTS[i], sb.value())

    def set_live_rpm(self, rpm):
        """Move the live dot to (rpm, interpolated_value)."""
        val = self.interpolate(rpm)
        self.dot_series.clear()
        self.dot_series.append(rpm, val)

    def interpolate(self, rpm):
        """Client-side linear interpolation of the current spinbox values at a given RPM."""
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

        # Scale Curve tab: value range 0..3, MCU encodes *1000
        self.scale_tab = CurveEditorTab(
            tmc_ui, axis, "scaleCurve", "Scale", 0.0, 3.0, 0.05, scale=1000.0, decimals=3)
        self.tabs.addTab(self.scale_tab, "Scale Curve")

        # Phase Advance tab: value in degrees, MCU encodes *100
        self.phase_tab = CurveEditorTab(
            tmc_ui, axis, "phaseAdvCurve", "Phase Advance (deg)", -10.0, 45.0, 0.5, scale=100.0, decimals=2)
        self.tabs.addTab(self.phase_tab, "Phase Advance")

        # Live RPM dot polling
        self.live_timer = QTimer(self)
        self.live_timer.timeout.connect(self._poll_live)
        self.current_rpm = 0.0

        # Auto-load both curves from MCU on open
        self._load_curves()

    def showEvent(self, event):
        super().showEvent(event)
        self.live_timer.start(200)

    def hideEvent(self, event):
        self.live_timer.stop()
        super().hideEvent(event)

    def _load_curves(self):
        self.tmc_ui.get_value_async("tmc", "scaleCurve", self._scale_curve_cb, self.axis, str)
        self.tmc_ui.get_value_async("tmc", "phaseAdvCurve", self._phase_curve_cb, self.axis, str)

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
        self.scale_tab.set_values(self._parse_curve(data, 1000.0))

    def _phase_curve_cb(self, data):
        self.phase_tab.set_values(self._parse_curve(data, 100.0))

    def _poll_live(self):
        # Fetch the current RPM via the acttrq reply (index 5 = measured_rpm)
        self.tmc_ui.get_value_async("tmc", "acttrq", self._acttrq_cb, self.axis, str)

    def _acttrq_cb(self, data):
        try:
            parts = str(data).split(":")
            if len(parts) >= 6:
                self.current_rpm = abs(float(parts[5]))
        except Exception:
            return
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

