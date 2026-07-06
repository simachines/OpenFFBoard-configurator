from PyQt6.QtWidgets import QMessageBox,QVBoxLayout,QGroupBox,QComboBox,QLabel,QApplication,QDialog,QTextEdit,QPushButton,QFileDialog
from PyQt6.QtWidgets import QSlider, QDoubleSpinBox, QFormLayout, QHBoxLayout, QWidget, QGridLayout, QSpinBox, QScrollArea, QTabWidget, QSizePolicy, QCheckBox
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
        self.cogging_harmonics_data_profiles = {}  # profile index -> [(order, amplitude, phase), ...]
        self.cogging_harmonics_data = []  # [(order, amplitude, phase), ...] — current profile (for backwards compat)
        self.cogging_rpm_targets = {}  # profile index -> measured RPM
        self._cw_harmonics_profiles = {}  # per-profile CW raw harmonics
        self._ccw_harmonics_profiles = {}  # per-profile CCW raw harmonics
        # Spatial bin snapshots from firmware (per-profile, per-direction).
        # Keys: "cw","ccw","ver_cw","ver_ccw" -> list of 720 float means.
        self._cw_bins_profiles = {}
        self._ccw_bins_profiles = {}
        self._ver_cw_bins_profiles = {}
        self._ver_ccw_bins_profiles = {}
        # Verification residual DFT top-20 per profile/direction: [(order,amp,phase),...]
        self._ver_cw_harm_profiles = {}
        self._ver_ccw_harm_profiles = {}
        self._pending_bins_adr = None  # which adr (0-5) is being requested
        self._profile_cw_lock = {}  # per-profile bool: True=first iteration CW done (stop accumulating)
        self._pending_harmonics_adr = None  # None = no request pending, 0/1/2 = requesting this adr
        self.cogging_profile_count = 3  # cached from firmware
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
        # Fast position update timer for cogging tab profile chart
        self.timer_pos = QTimer(self)
        self.timer_pos.timeout.connect(self._update_tab_profile_pos)
        self.timer_pos.setInterval(20)
    
        self.pushButton_align.clicked.connect(self.alignEnc)
        self.pushButton_autotunepid.clicked.connect(self.autotunePid)
        self.pushButton_cogging.clicked.connect(self.coggingDetection)
        # Hide the cogging button on the TMC4671 tab — now in Cogging Calibration dialog
        self.pushButton_cogging.setVisible(False)
        # Hide reset/reload table buttons (now in dialog)
        self.pushButton_resetCoggingTable.setVisible(False)
        self.pushButton_reloadCoggingTable.setVisible(False)
        self.tabWidget.currentChanged.connect(self.tabChanged)
        # Hide magnitude slider/spinbox (wired through .ui file)
        self.horizontalSlider_coggmag.hide()
        self.doubleSpinBox_coggScale.hide()
        # Re-enable cogging tab (tab_6) — shows torque/angle profile chart
        self.tabWidget.setTabEnabled(1, True)
        # Hide the old bar chart widget (chart_cogging was removed)
        self.graphWidget_Cogging.hide()
        # Remove the graphWidget_Cogging title label if present
        if hasattr(self, 'label_coggingTitle'):
            self.label_coggingTitle.hide()

        # ---- Cogging tab: Graph visibility checkboxes ----
        vis_layout = QHBoxLayout()
        self.chk_show_combined = QCheckBox("Combined")
        self.chk_show_combined.setChecked(True)
        self.chk_show_combined.toggled.connect(self._on_cogtab_vis_changed)
        vis_layout.addWidget(self.chk_show_combined)
        self.chk_show_cw = QCheckBox("CW Raw")
        self.chk_show_cw.setChecked(True)
        self.chk_show_cw.toggled.connect(self._on_cogtab_vis_changed)
        vis_layout.addWidget(self.chk_show_cw)
        self.chk_show_ccw = QCheckBox("CCW Raw")
        self.chk_show_ccw.setChecked(True)
        self.chk_show_ccw.toggled.connect(self._on_cogtab_vis_changed)
        vis_layout.addWidget(self.chk_show_ccw)
        self.chk_show_cogging = QCheckBox("Cogging Torque")
        self.chk_show_cogging.setChecked(True)
        self.chk_show_cogging.toggled.connect(self._on_cogtab_vis_changed)
        vis_layout.addWidget(self.chk_show_cogging)
        self.chk_show_pos = QCheckBox("Position")
        self.chk_show_pos.setChecked(True)
        self.chk_show_pos.toggled.connect(self._on_cogtab_vis_changed)
        vis_layout.addWidget(self.chk_show_pos)
        # Insert visibility row into the cogging tab layout
        tab = self.tabWidget.widget(1)
        if tab and tab.layout():
            vis_widget = QWidget()
            vis_widget.setLayout(vis_layout)
            # Insert as a row at the bottom of the cogging tab's layout
            parent_layout = tab.layout()
            if isinstance(parent_layout, QGridLayout):
                # Find the last used row and add after it
                last_row = parent_layout.rowCount()
                parent_layout.addWidget(vis_widget, last_row, 0, 1, -1)
            else:
                parent_layout.addWidget(vis_widget)
        #self.initUi()

        self.pushButton_scaleTune = QPushButton("Cogging Calibration")
        self.pushButton_scaleTune.clicked.connect(self.openScaleCurveDialog)
        if hasattr(self, 'groupBox_anticogging'):
            formLayout = self.groupBox_anticogging.layout()
            if formLayout:
                formLayout.addRow(self.pushButton_scaleTune)

        self.timer.timeout.connect(self.updateTimer)
        self.timer_status.timeout.connect(self.updateStatus)

        # Reliable tab switch detection via main window's tab widget
        self.main.tabWidget_main.currentChanged.connect(self._on_main_tab_changed)

   
        # Chart setup (amps/temps chart)
        self.chart = QChart()
        self.chart.setBackgroundRoundness(5)
        self.chart.setMargins(QMargins(0,0,0,0))
        self.chartXaxis = QValueAxis(self.chart)
        self.chartXaxis.setGridLineColor(QColor(QApplication.instance().palette().dark().color().red(),QApplication.instance().palette().dark().color().green(),QApplication.instance().palette().dark().color().blue(),128))
        
        self.chart.addAxis(self.chartXaxis,Qt.AlignmentFlag.AlignBottom)

        self.chartYaxis_Amps = QValueAxis(self.chart)
        self.chartYaxis_Temps = QValueAxis(self.chart)
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
        self.graphWidget_Amps.setChart(self.chart)

        self.chart.legend().setVisible(False)

        # --- Torque/Angle Profile Chart in Cogging tab (tab_6) ---
        self.chart_cogging_profile = QChart()
        self.chart_cogging_profile.setBackgroundRoundness(5)
        self.chart_cogging_profile.setMargins(QMargins(0,0,0,0))

        self.chart_cp_Xaxis = QValueAxis(self.chart_cogging_profile)
        self.chart_cp_Xaxis.setRange(0, 360)
        self.chart_cp_Xaxis.setTitleText("Angle (deg)")
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

        # Measured Cogging Torque (orange dashed)
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

        self.chart_cogging_profile.legend().setVisible(True)
        self.chart_cogging_profile.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)

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
        self.register_callback("tmc","coggingBins",self.updateCoggingBins,self.axis,str)
        
        self.checkBox_combineEncoders.stateChanged.connect(self.extEncoderChanged)

        # Track the open scale dialog (non-modal)
        self._scale_dlg = None

    def _on_cogtab_vis_changed(self):
        """Toggle series visibility on the TMC cogging tab chart."""
        self.line_cp_waveform.setVisible(self.chk_show_combined.isChecked())
        self.line_cp_cw.setVisible(self.chk_show_cw.isChecked())
        self.line_cp_ccw.setVisible(self.chk_show_ccw.isChecked())
        self.line_cp_cogging.setVisible(self.chk_show_cogging.isChecked())
        self.scatter_cp_pos.setVisible(self.chk_show_pos.isChecked())
        self.line_cp_vmarker.setVisible(self.chk_show_pos.isChecked())

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
                self.timer_pos.start()
        else:
            self.timer.stop()
            self.timer_status.stop()
            self.timer_pos.stop()

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
        self.checkBox_cogging.setEnabled(cogging_enabled)
        self.groupBox_anticogging.setEnabled(cogging_enabled)
        self.tabWidget.setTabEnabled(1, cogging_enabled)
        self.syncHarmonicEditor()

        self.checkBox_cogging.setChecked(self.anti_coggingEnable)
        if self.anti_coggingEnable:
            if not supported_motor:
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

        self.label_encoder_notice.setVisible(data == 5 or data == 4 or data == 3 or data == 2)
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
        iqcmd = None
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
        vel_rpm = 0
        if len(tflist) >= 6:
            iqcmd = tflist[5]  # pre-anti-cogging torque setpoint (iqCmd)
        if len(tflist) >= 7:
            vel_rpm = int(tflist[6])  # velocity RPM from MCU
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
                if iqcmd is not None:
                    iq_amps = iqcmd * self.adc_to_amps
                    txt += f"\nIqCmd: {iq_amps:+.3f}A"
                if cogging is not None:
                    c_amps = cogging * self.adc_to_amps
                    txt += f"\nCogging: {c_amps:+.3f}A"
                if flux is not None:
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

    def _update_tab_profile_pos(self):
        """Fast 20ms position dot update for cogging tab profile chart (tab_6)."""
        if not self.tabWidget.isTabEnabled(1):
            return
        if self.tabWidget.currentIndex() != 1:
            return
        self.updateProfilePosition()

    def updateCogging(self,data):
        try:
            if "data" in data:
                item_str, data_str = data.split(',', 1)
                start_index = int(item_str.split(':')[1])
                values_str = data_str.split('(')[1].split(')')[0]
                points = [float(p) for p in values_str.split(',') if p]

                for i, p in enumerate(points):
                    if start_index + i < len(self.cogging_data):
                        self.cogging_data[start_index + i] = p
                        self.cogging_data_received[start_index + i] = True

        except Exception as e:
            self.main.log("TMC cogging update error: " + str(e))

    def updateTemp(self,t):
        t = t/100.0
        if(t > 150 or t < -20):
            return
        self.label_Temp.setText(str(round(t,2)) + "°C")
        self.lines_Temps.append(self.chartLastX+1,t)
        if(self.lines_Temps.count() > self.max_datapoints):
            self.lines_Temps.remove(0)
        
        if(t > self.chartYaxis_Temps.max()):
            self.chartYaxis_Temps.setMax(round(t))
    
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
            self.versionWarningShow = False
            QTimer.singleShot(100,self.showVersionSelectorPopup)
        else:
            self.versionWarningShow = False

    def init_ui(self):
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
            self.send_commands("tmc",["mtype","encsrc","tmcHwType","trqbq_mode"],self.axis,'!')
            self.send_commands("tmc",["tmctype","tmcHwType","iScale","calibrated","trqbq_f","coggingScale","coggingShape"],self.axis)
            self.send_command("tmc","cogging",self.axis,'?')
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

            if self.tabWidget.currentWidget() == self.tabWidget.widget(1):
                self.reloadCoggingTable()
            else:
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
            self.timer_pos.stop()
            self.ui_initialized = False
        else:
            self.groupBox_tmc.setTitle(type)
            self.setEnabled(True)

    def calibrated(self,v):
        v = int(v)
        if not v and self.isEnabled() and self.comboBox_mtype.currentIndex() != 0 and self.comboBox_enc.currentIndex() != 0:
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
            if msg_text.startswith('("') and msg_text.endswith(')'):
                parts = msg_text.rsplit('",', 1)
                if len(parts) == 2:
                    text_part = parts[0][2:]
                    try:
                        status = int(parts[1][:-1])
                        msg_text = text_part
                    except ValueError:
                        pass

            # Track which RPM profile is being calibrated
            if msg_text.startswith("RPM profile "):
                try:
                    # "RPM profile 2/3: target 30.0 RPM, 1 iterations ..."
                    prof_part = msg_text[12:].split("/")[0].strip()
                    prof_num = int(prof_part)
                    self._active_cw_profile = prof_num
                    self._active_ccw_profile = prof_num
                    # Track max profile seen and update harmonic editor spinbox if needed
                    if prof_num > self.cogging_profile_count:
                        self.cogging_profile_count = prof_num
                        # Update harmonic editor spinbox range if dialog is open
                        if hasattr(self, '_scale_dlg') and self._scale_dlg is not None:
                            self._scale_dlg.h3_tab.spin_rpm_profile.setRange(1, prof_num)
                except Exception:
                    pass

            # Parse CW/CCW — do NOT show in popup
            is_cw_ccw = msg_text.startswith("CWD:") or msg_text.startswith("CCWD:")
            
            if not is_cw_ccw:
                if self.cogging_text:
                    self.cogging_text += "\n"
                self.cogging_text += msg_text
            
            if self.cogging_dialog is None:
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
                def on_finish():
                    self.cogging_dialog = None
                    self.cogging_text = ""
                    self.cogging_text_edit = None
                    if not self.cogging_calibrating:
                        self.timer.start(50)
                        self.timer_status.start(250)
                        self.timer_pos.start()
                self.cogging_dialog.finished.connect(on_finish)
            
            self.cogging_text_edit.setText(self.cogging_text)
            self.cogging_text_edit.verticalScrollBar().setValue(self.cogging_text_edit.verticalScrollBar().maximum())
            
            if status == 1:
                self.cogging_calibrating = False
                self.timer.start(50)
                self.timer_status.start(250)
                self.timer_pos.start()
                self.reloadCoggingTable()
                self.send_command("tmc", "cogging", self.axis, '?')
                self.send_command("tmc", "coggingCwCcw", self.axis, '?')
                # Request harmonic data for all profiles so the harmonic editor has them
                for adr in range(0, 5):
                    self._pending_harmonics_adr = adr
                    self.send_command("tmc", "coggingHarmonics", self.axis, '?', adr=adr)
                # Re-read auto-tuned PID values after calibration completes
                if hasattr(self, '_scale_dlg') and self._scale_dlg is not None:
                    self._scale_dlg.cogging_cal_tab.sync_pid_values()

            # Parse CW/CCW silently — accumulate chunks per profile.
            # Firmware broadcasts CW/CCW data in multiple 100-char chunks per
            # direction per DFT iteration.  CWD: chunks arrive first, then CCWD:.
            # When we see CWD: after CCWD: has already been stored for this
            # profile, a new iteration has started — stop accumulating (first
            # iteration has the full uncompensated cogging; later iterations
            # have the anti-cogging table fed back as residual).
            if is_cw_ccw:
                try:
                    is_cw = msg_text.startswith("CWD:")
                    prefix = "CWD:" if is_cw else "CCWD:"
                    data_str = msg_text[len(prefix):]
                    target_list = self.cw_raw_harmonics if is_cw else self.ccw_raw_harmonics
                    profile_idx = self._active_cw_profile

                    # Detect new iteration: CWD: after CCW already stored
                    if is_cw and self._profile_cw_lock.get(profile_idx, False):
                        # Don't store — this is iteration 2+ (residual)
                        pass
                    else:
                        new_data = []
                        for chunk in data_str.split(","):
                            chunk = chunk.strip()
                            if not chunk:
                                continue
                            parts = chunk.split(":")
                            if len(parts) == 3:
                                order = int(parts[0])
                                mag = float(parts[1])
                                phase = float(parts[2]) / 1000.0
                                if mag > 0.0:
                                    new_data.append((order, mag, phase))
                        if new_data:
                            if is_cw:
                                existing = self._cw_harmonics_profiles.get(profile_idx, [])
                                existing_orders = {h[0] for h in existing}
                                for entry in new_data:
                                    if entry[0] not in existing_orders:
                                        existing.append(entry)
                                        existing_orders.add(entry[0])
                                self._cw_harmonics_profiles[profile_idx] = existing
                                target_list.clear()
                                target_list.extend(existing)
                            else:
                                existing = self._ccw_harmonics_profiles.get(profile_idx, [])
                                existing_orders = {h[0] for h in existing}
                                for entry in new_data:
                                    if entry[0] not in existing_orders:
                                        existing.append(entry)
                                        existing_orders.add(entry[0])
                                self._ccw_harmonics_profiles[profile_idx] = existing
                                target_list.clear()
                                target_list.extend(existing)
                                # CCW data complete for this iteration — lock CW
                                self._profile_cw_lock[profile_idx] = True
                    self.rebuildCwCcwWaveforms()
                except Exception:
                    pass
        
    def coggingDetection(self):
        self.cogging_calibrating = True
        # Clear locally cached CW/CCW/DFT data from previous calibrations
        self._cw_harmonics_profiles.clear()
        self._ccw_harmonics_profiles.clear()
        self._profile_cw_lock.clear()
        self._cw_bins_profiles.clear()
        self._ccw_bins_profiles.clear()
        self._ver_cw_bins_profiles.clear()
        self._ver_ccw_bins_profiles.clear()
        self._ver_cw_harm_profiles.clear()
        self._ver_ccw_harm_profiles.clear()
        self.cogging_harmonics_data_profiles.clear()
        self.cw_raw_harmonics.clear()
        self.ccw_raw_harmonics.clear()
        self.cogging_harmonics_data.clear()
        self.cogging_rpm_targets.clear()
        self.clearCoggingGraph()
        self.timer.stop()
        self.timer_status.stop()
        self.timer_pos.stop()
        self.send_command("tmc","calibrateCogging", self.axis)
        self.main.log("Started cogging detection")

    def tabChanged(self, index):
        if self.tabWidget.widget(index) == self.tabWidget.widget(1):
            self.reloadCoggingTable()

    def clearCoggingGraph(self, keep_cw_ccw=False):
        self.cogging_data = [0] * 128
        self.cogging_data_received = [False] * 128
        self.clearCoggingProfile(keep_cw_ccw=keep_cw_ccw)

    def clearCoggingProfile(self, keep_cw_ccw=False):
        """Clear cogging profile data. Set keep_cw_ccw=True to preserve CW/CCW per-profile
        harmonics that were captured during calibration broadcasts."""
        self.cogging_harmonics_data = []
        if not keep_cw_ccw:
            self.cogging_harmonics_data_profiles = {}
            self.cogging_rpm_targets = {}
            self.cw_raw_harmonics = []
            self.ccw_raw_harmonics = []
            self._cw_harmonics_profiles = {}
            self._ccw_harmonics_profiles = {}
        self._active_cw_profile = 1  # which profile's CW/CCW is shown
        self._active_ccw_profile = 1
        self.line_cp_waveform.clear()
        self.line_cp_cw.clear()
        self.line_cp_ccw.clear()
        self.line_cp_cogging.clear()
        self.scatter_cp_pos.clear()
        self.line_cp_vmarker.clear()
        self.chart_cp_Yaxis.setMin(-10)
        self.chart_cp_Yaxis.setMax(10)
        self.syncHarmonicEditor()

    def syncHarmonicEditor(self):
        pass

    def updateHarmonicPreview(self):
        if not self.cogging_harmonics_data:
            self.line_cp_waveform.clear()
            self.line_cp_cogging.clear()
            self.scatter_cp_pos.clear()
            self.line_cp_vmarker.clear()
            return
        self.rebuildProfileWaveform()

    def updateCoggingHarmonics(self, data):
        try:
            # Detect spontaneous broadcast with "profile:N:" prefix from firmware
            # (sent immediately after each RPM profile's DFT completes during calibration)
            broadcast_profile = None
            if isinstance(data, str) and data.startswith("profile:"):
                parts = data.split(":", 2)
                if len(parts) >= 3:
                    try:
                        broadcast_profile = int(parts[1])
                        data = parts[2]  # remainder is the actual harmonic data
                    except ValueError:
                        pass

            if not data or data == "0:0:0":
                self.cogging_harmonics_data = []
            else:
                harmonics = []
                for item in data.split(","):
                    parts = item.split(":")
                    if len(parts) == 3:
                        order = int(parts[0])
                        amp = float(parts[1])
                        phase = float(parts[2]) / 1000.0
                        if order > 0 or amp > 0:
                            harmonics.append((order, amp, phase))
                self.cogging_harmonics_data = harmonics

            # Handle spontaneous broadcast: store in per-profile map immediately
            if broadcast_profile is not None:
                self.cogging_harmonics_data_profiles[broadcast_profile] = list(self.cogging_harmonics_data)
                # Notify the HarmShapingTab if dialog is open
                if hasattr(self, '_scale_dlg') and self._scale_dlg is not None:
                    self._scale_dlg.h3_tab._profile_data_loaded = True
                    self._scale_dlg.h3_tab._y_range_locked = False
                    self._scale_dlg.h3_tab.redraw()
                    self._scale_dlg.h3_tab._rebuild_bar_chart()
                    self._scale_dlg.h3_tab._rebuild_magnitude_editors()

            # Route to pending profile request if active (solicited query)
            if self._pending_harmonics_adr is not None:
                profile_idx = self._pending_harmonics_adr + 1
                self.cogging_harmonics_data_profiles[profile_idx] = list(self.cogging_harmonics_data)
                self._pending_harmonics_adr = None
                # Notify the HarmShapingTab if dialog is open
                if hasattr(self, '_scale_dlg') and self._scale_dlg is not None:
                    self._scale_dlg.h3_tab._profile_data_loaded = True
                    self._scale_dlg.h3_tab._y_range_locked = False
                    self._scale_dlg.h3_tab.redraw()
                    self._scale_dlg.h3_tab._rebuild_bar_chart()
                    self._scale_dlg.h3_tab._rebuild_magnitude_editors()

            self.syncHarmonicEditor()
            self.updateHarmonicPreview()
        except Exception as e:
            self.main.log("TMC cogging harmonics parse error: " + str(e))

    def updateCwCcwData(self, data):
        """Handle coggingCwCcw query response from firmware.
        IMPORTANT: Only stores data if the target profile does NOT already have
        CW/CCW data from a calibration broadcast. The firmware only keeps the LAST
        profile's data, so this query response would otherwise overwrite earlier
        profiles' correctly-captured data."""
        try:
            if not data:
                return
            cw_list = []
            ccw_list = []

            parts = data.split("|")
            for part in parts:
                if part.startswith("CW:"):
                    data_str = part[3:]
                    if data_str != "0:0:0":
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
                                    cw_list.append((order, amp, phase))
                elif part.startswith("CCW:"):
                    data_str = part[4:]
                    if data_str != "0:0:0":
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
                                    ccw_list.append((order, amp, phase))

            # Only store if the profile doesn't already have calibration-broadcast data
            # (firmware's single-set coggingCwCcw response only has last profile's data)
            profile_idx = self._active_cw_profile
            if cw_list and not self._cw_harmonics_profiles.get(profile_idx):
                self._cw_harmonics_profiles[profile_idx] = cw_list
            if ccw_list and not self._ccw_harmonics_profiles.get(profile_idx):
                self._ccw_harmonics_profiles[profile_idx] = ccw_list

            # Always keep the active list updated for the TMC cogging tab chart
            self.cw_raw_harmonics = list(self._cw_harmonics_profiles.get(profile_idx, []))
            self.ccw_raw_harmonics = list(self._ccw_harmonics_profiles.get(profile_idx, []))
            self.rebuildCwCcwWaveforms()
        except Exception as e:
            self.main.log("TMC CW/CCW parse error: " + str(e))

    def updateCoggingBins(self, data):
        """Handle coggingBins chunked response from firmware.
        Each reply is self-describing via a 'B<adr>:' prefix:
          adr 0-3: 'B<adr>:item:<offset>,data:(v0,v1,...)' repeated chunks
          adr 4-5: 'B<adr>:order:amp:phase,order:amp:phase,...' (top-20 DFT)
        Special: 'NOBINS' means no bin data available."""
        try:
            if not data:
                return
            if data == "NOBINS":
                self._pending_bins_adr = None
                return
            profile_idx = self._active_cw_profile
            # Parse the 'B<adr>:' prefix.
            if not data.startswith("B"):
                return
            colon = data.find(":")
            if colon < 0:
                return
            try:
                adr = int(data[1:colon])
            except ValueError:
                return
            payload = data[colon + 1:]

            if adr <= 3:
                # Bin array chunk: 'item:<off>,data:(v0,v1,...)'
                if not payload.startswith("item:"):
                    return
                comma = payload.find(",")
                if comma < 0:
                    return
                off = int(payload[5:comma])
                data_part = payload[comma + 1:]
                if not data_part.startswith("data:("):
                    return
                vals_str = data_part[6:].rstrip(")")
                vals = [int(v) for v in vals_str.split(",") if v]

                # Pick the right per-profile dict
                if adr == 0:
                    d = self._cw_bins_profiles.setdefault(profile_idx, [0.0] * 720)
                elif adr == 1:
                    d = self._ccw_bins_profiles.setdefault(profile_idx, [0.0] * 720)
                elif adr == 2:
                    d = self._ver_cw_bins_profiles.setdefault(profile_idx, [0.0] * 720)
                else:
                    d = self._ver_ccw_bins_profiles.setdefault(profile_idx, [0.0] * 720)
                for i, v in enumerate(vals):
                    idx = off + i
                    if 0 <= idx < 720:
                        d[idx] = float(v)
            elif adr == 4 or adr == 5:
                # Verification top-20 DFT
                harm_list = []
                if payload != "0:0:0":
                    for item in payload.split(","):
                        item = item.strip()
                        if not item:
                            continue
                        parts = item.split(":")
                        if len(parts) == 3:
                            order = int(parts[0])
                            amp = float(parts[1])
                            phase = float(parts[2]) / 1000.0
                            if amp > 0.0:
                                harm_list.append((order, amp, phase))
                if adr == 4:
                    self._ver_cw_harm_profiles[profile_idx] = harm_list
                else:
                    self._ver_ccw_harm_profiles[profile_idx] = harm_list
        except Exception as e:
            self.main.log("TMC coggingBins parse error: " + str(e))

    def requestCoggingBins(self, profile_idx=None):
        """Request all bin datasets from firmware for the given (or current) profile."""
        if profile_idx is None:
            profile_idx = self._active_cw_profile
        self._active_cw_profile = profile_idx
        for adr in range(0, 6):
            self._pending_bins_adr = adr
            self.send_command("tmc", "coggingBins", self.axis, '?', adr=adr)

    def rebuildProfileWaveform(self):
        self.line_cp_waveform.clear()
        self.line_cp_cogging.clear()
        if not self.cogging_harmonics_data:
            return

        max_amp = 0.0
        for deg in range(0, 361):
            theta = math.radians(deg)
            green = 0.0
            orange = 0.0
            for order, amp, phase in self.cogging_harmonics_data:
                green  += amp * math.sin(order * theta + phase)
                orange -= amp * math.cos(order * theta + phase)
            self.line_cp_waveform.append(float(deg), float(green))
            self.line_cp_cogging.append(float(deg), float(orange))
            max_amp = max(max_amp, abs(green), abs(orange))

        current_max = max(abs(self.chart_cp_Yaxis.min()), abs(self.chart_cp_Yaxis.max()))
        margin = max(max_amp * 1.2, current_max, 10.0)
        self.chart_cp_Yaxis.setRange(-margin, margin)
        self.updateProfilePosition()

    def onHarmonicMagnitudeChanged(self, index, value):
        pass

    def on_pot_scale_changed(self, _val=None):
        self.pot_scale = 1.0
        self.rebuildProfileWaveform()

    def rebuildCwCcwWaveforms(self):
        self.line_cp_cw.clear()
        self.line_cp_ccw.clear()
        if not self.cw_raw_harmonics and not self.ccw_raw_harmonics:
            return
        max_cw_ccw = 0.0
        if self.cw_raw_harmonics:
            for deg in range(0, 361):
                theta = math.radians(deg)
                v = 0.0
                for order, amp, phase in self.cw_raw_harmonics:
                    v += amp * math.sin(order * theta + phase)
                self.line_cp_cw.append(float(deg), float(v))
                if abs(v) > max_cw_ccw:
                    max_cw_ccw = abs(v)
        if self.ccw_raw_harmonics:
            for deg in range(0, 361):
                theta = math.radians(deg)
                v = 0.0
                for order, amp, phase in self.ccw_raw_harmonics:
                    v += amp * math.sin(order * theta + phase)
                self.line_cp_ccw.append(float(deg), float(v))
                if abs(v) > max_cw_ccw:
                    max_cw_ccw = abs(v)
        # Scale Y-axis to include CW/CCW waveforms
        current_max = max(abs(self.chart_cp_Yaxis.min()), abs(self.chart_cp_Yaxis.max()))
        margin = max(max_cw_ccw * 1.2, current_max, 10.0)
        self.chart_cp_Yaxis.setRange(-margin, margin)


    def applyHarmonicMagnitudes(self):
        pass

    def updateProfilePosition(self):
        pos_deg = self.cogging_position * 360.0
        torque = self.cogging_measured_torque
        
        self.scatter_cp_pos.clear()
        self.scatter_cp_pos.append(pos_deg, torque)
        
        y_min = self.chart_cp_Yaxis.min()
        y_max = self.chart_cp_Yaxis.max()
        self.line_cp_vmarker.clear()
        self.line_cp_vmarker.append(pos_deg, y_min)
        self.line_cp_vmarker.append(pos_deg, y_max)

    def resetCoggingTable(self):
        self.send_value("tmc", "coggingTable", 0, instance=self.axis)
        self.clearCoggingGraph()

    def reloadCoggingTable(self):
        # Preserve CW/CCW per-profile data captured during calibration broadcasts.
        # The firmware only keeps the last profile's CW/CCW, so clearing would lose
        # data for other profiles. Use keep_cw_ccw=True to preserve them.
        # Do NOT query coggingCwCcw here — the firmware only has the LAST
        # profile's data in cw_store/ccw_store, and _active_cw_profile defaults
        # to 1 on restart.  That would overwrite profile 1's data with the last
        # profile's harmonics.
        self.clearCoggingGraph(keep_cw_ccw=True)
        self.send_command("tmc", "coggingTable", self.axis, '?')
        self._pending_harmonics_adr = 0
        self.send_command("tmc", "coggingHarmonics", self.axis, '?', adr=0)

    def coggingScaleCb(self, val):
        pass

    def openScaleCurveDialog(self):
        if hasattr(self, '_scale_dlg') and self._scale_dlg is not None:
            self._scale_dlg.raise_()
            self._scale_dlg.activateWindow()
            return
        self._scale_dlg = ScalePhaseAdvanceDialog(self, self.axis)
        self._scale_dlg.finished.connect(self._on_scale_dlg_closed)
        self._scale_dlg.show()

    def _on_scale_dlg_closed(self, result=0):
        self._scale_dlg = None

    def coggingShapeCb(self, val):
        pass

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
    """One editable speed-dependent curve: a chart with a live RPM dot plus per-RPM spinboxes."""
    RPM_POINTS = [0,3,7,10,12,15,20,25,30,35,40,50,60,70,80,90,100,120,140,160,180,200,225,256]

    def __init__(self, tmc_ui, axis, cmd_name, y_label, y_min, y_max, y_step, scale, decimals):
        super().__init__()
        self.tmc_ui = tmc_ui
        self.axis = axis
        self.cmd_name = cmd_name
        self.scale = float(scale)
        self._y_min = float(y_min)
        self._y_max = float(y_max)
        self._loading = False
        self._shaping_sync = False

        self.base_values = [0.0] * len(self.RPM_POINTS)
        self.target_begin = 0.0
        self.target_end = 0.0
        self.knee_idx = 0
        self._slider_dragging = False
        self._send_debounce = QTimer(self)
        self._send_debounce.setSingleShot(True)
        self._send_debounce.setInterval(250)
        self._send_debounce.timeout.connect(self._debounced_send)

        layout = QVBoxLayout(self)

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

        palette_color = QApplication.instance().palette().window().color()
        self.chart.setBackgroundBrush(palette_color)
        self.chartView = QChartView(self.chart)
        self.chartView.setMinimumHeight(220)

        chart_row = QHBoxLayout()

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

        self.lbl_status = QLabel("Begin: --   End: --   Knee: 0 RPM")
        layout.addWidget(self.lbl_status)

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

    def _on_slider_press(self):
        self._slider_dragging = True
        self._send_debounce.stop()

    def _on_slider_release(self):
        self._slider_dragging = False
        self._send_debounce.stop()
        self._apply_shaping(send=True)

    def _debounced_send(self):
        if not self._slider_dragging:
            self._apply_shaping(send=True)

    def _slider_to_value(self, slider_val):
        return self._y_min + (slider_val / 100.0) * (self._y_max - self._y_min)

    def _value_to_slider(self, val):
        span = self._y_max - self._y_min
        if span <= 0:
            return 0
        return int(round((val - self._y_min) / span * 100.0))

    def _on_begin_slider(self, val):
        if self._shaping_sync:
            return
        self.target_begin = self._slider_to_value(val)
        self._apply_shaping(send=False)
        if not self._slider_dragging:
            self._send_debounce.start()

    def _on_end_slider(self, val):
        if self._shaping_sync:
            return
        self.target_end = self._slider_to_value(val)
        self._apply_shaping(send=False)
        if not self._slider_dragging:
            self._send_debounce.start()

    def _on_begin_spin(self, val):
        if self._shaping_sync:
            return
        self.target_begin = float(val)
        self._apply_shaping(send=True)

    def _on_end_spin(self, val):
        if self._shaping_sync:
            return
        self.target_end = float(val)
        self._apply_shaping(send=True)

    def _on_knee_slider(self, val):
        if self._shaping_sync:
            return
        self.knee_idx = int(val)
        self._apply_shaping(send=False)
        if not self._slider_dragging:
            self._send_debounce.start()

    def _on_knee_spin(self, val):
        if self._shaping_sync:
            return
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

    def _apply_shaping(self, send=False):
        if not self.base_values:
            return

        N = len(self.RPM_POINTS)
        knee_idx = max(0, min(self.knee_idx, N - 1))
        rpm_knee = self.RPM_POINTS[knee_idx]
        rpm_last = self.RPM_POINTS[-1]
        rpm_span = rpm_last - rpm_knee

        self._loading = True
        for i, sb in enumerate(self.spinboxes):
            rpm_i = self.RPM_POINTS[i]
            if i < knee_idx:
                v = self.target_begin
            else:
                if rpm_span <= 0:
                    v = self.target_end if i == N - 1 else self.target_begin
                else:
                    t = (rpm_i - rpm_knee) / rpm_span
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

    def set_values(self, float_values):
        self._loading = True
        for i, sb in enumerate(self.spinboxes):
            if i < len(float_values):
                sb.setValue(float(float_values[i]))
        self._loading = False
        self.base_values = [sb.value() for sb in self.spinboxes]
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
        self.base_values = [sb.value() for sb in self.spinboxes]
        self.target_begin = self.base_values[0] if self.base_values else 0.0
        self.target_end = self.base_values[-1] if self.base_values else 0.0
        self.knee_idx = 0
        self._reset_shaping_sliders()
        self._update_knee_marker()
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
    """Editor for cogging waveshaping parameters with harmonics bar chart and RPM profile selector."""

    def __init__(self, tmc_ui, axis):
        super().__init__()
        self.tmc_ui = tmc_ui
        self.axis = axis
        self._loading = False

        layout = QVBoxLayout(self)

        # RPM profile selector
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Edit RPM profile #:"))
        self.spin_rpm_profile = QSpinBox()
        self.spin_rpm_profile.setRange(1, max(1, tmc_ui.cogging_profile_count))
        self.spin_rpm_profile.setValue(1)
        self.spin_rpm_profile.setToolTip("Select which RPM calibration profile's harmonics to edit")
        self.spin_rpm_profile.valueChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self.spin_rpm_profile)
        profile_row.addStretch(1)
        layout.addLayout(profile_row)

        # Chart toggle
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(QLabel("Chart view:"))
        self.combo_chart_view = QComboBox()
        self.combo_chart_view.addItem("Waveform (angle)")
        self.combo_chart_view.addItem("Harmonic Magnitudes")
        self.combo_chart_view.currentIndexChanged.connect(self._on_view_changed)
        toggle_row.addWidget(self.combo_chart_view)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        # Stacked charts
        self.stack = QWidget()
        self.stack_layout = QVBoxLayout(self.stack)
        self.stack_layout.setContentsMargins(0,0,0,0)

        # Waveform chart
        self.chart_wave = QChart()
        self.chart_wave.setMargins(QMargins(2, 2, 2, 2))
        self.chart_wave.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.axisX_wave = QValueAxis()
        self.axisX_wave.setTitleText("Angle (deg)")
        self.axisX_wave.setRange(0, 360)
        self.axisY_wave = QValueAxis()
        self.axisY_wave.setTitleText("Compensation")
        self.chart_wave.addAxis(self.axisX_wave, Qt.AlignmentFlag.AlignBottom)
        self.chart_wave.addAxis(self.axisY_wave, Qt.AlignmentFlag.AlignLeft)

        self.orig_series = QLineSeries()
        self.orig_series.setName("Original")
        self.orig_series.setColor(QColor("#3daee9"))
        self.chart_wave.addSeries(self.orig_series)
        self.orig_series.attachAxis(self.axisX_wave)
        self.orig_series.attachAxis(self.axisY_wave)

        self.shaped_series = QLineSeries()
        self.shaped_series.setName("Shaped")
        self.shaped_series.setColor(QColor("limegreen"))
        pen = self.shaped_series.pen()
        pen.setWidth(2)
        self.shaped_series.setPen(pen)
        self.chart_wave.addSeries(self.shaped_series)
        self.shaped_series.attachAxis(self.axisX_wave)
        self.shaped_series.attachAxis(self.axisY_wave)

        # Position dot (live position marker on waveform)
        self.scatter_pos = QScatterSeries()
        self.scatter_pos.setName("Position")
        self.scatter_pos.setColor(QColor("red"))
        self.scatter_pos.setMarkerSize(10)
        self.chart_wave.addSeries(self.scatter_pos)
        self.scatter_pos.attachAxis(self.axisX_wave)
        self.scatter_pos.attachAxis(self.axisY_wave)

        # Vertical position line
        self.line_vmarker = QLineSeries()
        self.line_vmarker.setName("")
        self.line_vmarker.setColor(QColor("red"))
        pen = self.line_vmarker.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        self.line_vmarker.setPen(pen)
        self.chart_wave.addSeries(self.line_vmarker)
        self.line_vmarker.attachAxis(self.axisX_wave)
        self.line_vmarker.attachAxis(self.axisY_wave)

        self.view_wave = QChartView(self.chart_wave)
        self.view_wave.setMinimumHeight(240)

        # Harmonics bar chart
        self.chart_bars = QChart()
        self.chart_bars.setMargins(QMargins(2, 2, 2, 2))
        self.chart_bars.legend().hide()
        self.axisX_bars = QValueAxis()
        self.axisX_bars.setTitleText("Harmonic order")
        self.axisX_bars.setRange(1, 128)
        self.axisY_bars = QValueAxis()
        self.axisY_bars.setTitleText("Amplitude")
        self.axisY_bars.setMin(0)
        self.axisY_bars.setMax(10)
        self.chart_bars.addAxis(self.axisX_bars, Qt.AlignmentFlag.AlignBottom)
        self.chart_bars.addAxis(self.axisY_bars, Qt.AlignmentFlag.AlignLeft)

        self.bar_set = QBarSet("Harmonics")
        self.bar_series = QBarSeries()
        self.bar_series.append(self.bar_set)
        self.chart_bars.addSeries(self.bar_series)
        self.bar_set.setColor(QColor("cornflowerblue"))
        self.bar_series.attachAxis(self.axisY_bars)
        self.bar_series.attachAxis(self.axisX_bars)

        self.view_bars = QChartView(self.chart_bars)
        self.view_bars.setMinimumHeight(240)

        self.stack_layout.addWidget(self.view_wave)
        self.stack_layout.addWidget(self.view_bars)
        self.view_bars.hide()
        layout.addWidget(self.stack, 1)

        # Controls
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Per-harmonic magnitude editor (scrollable)
        self.harm_scroll = QScrollArea()
        self.harm_scroll.setWidgetResizable(True)
        self.harm_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.harm_scroll.setMinimumHeight(60)
        self.harm_scroll.setMaximumHeight(260)
        self.harm_container = QWidget()
        self.harm_container_layout = QVBoxLayout(self.harm_container)
        self.harm_container_layout.setContentsMargins(0, 0, 0, 0)
        self.harm_scroll.setWidget(self.harm_container)
        self._harm_spinboxes = []  # [(order_label, amp_spinbox), ...]
        form.addRow("Harmonic magnitudes:", self.harm_scroll)

        layout.addLayout(form)

        self._current_profile = 1
        self._profile_data_loaded = False

        # Profile info label
        self.label_profile_info = QLabel("")
        form.addRow("", self.label_profile_info)

        # Angle readout (live position in degrees)
        self.label_angle = QLabel("Angle: ---°")
        self.label_angle.setStyleSheet("font-weight: bold; font-size: 13px;")
        form.addRow("", self.label_angle)

        # "Show CW/CCW direction graphs" checkbox
        self.chk_show_dir_graphs = QCheckBox("Show CW/CCW direction graphs")
        self.chk_show_dir_graphs.setChecked(False)
        self.chk_show_dir_graphs.setToolTip("Overlay the CW and CCW raw harmonic waveforms "
                                              "on the chart for comparison")
        self.chk_show_dir_graphs.toggled.connect(self._on_show_dir_graphs_toggled)
        form.addRow("", self.chk_show_dir_graphs)

        # Download / Copy buttons
        btn_row = QHBoxLayout()
        self.btn_download_data = QPushButton("Download CW/CCW Data")
        self.btn_download_data.setToolTip("Save the current profile's CW, CCW and DFT harmonic "
                                           "data to a text file")
        self.btn_download_data.clicked.connect(self._on_download_data)
        btn_row.addWidget(self.btn_download_data)
        self.btn_copy_data = QPushButton("Copy to Clipboard")
        self.btn_copy_data.setToolTip("Copy the current profile's CW, CCW and DFT harmonic "
                                       "data to the clipboard")
        self.btn_copy_data.clicked.connect(self._on_copy_data)
        btn_row.addWidget(self.btn_copy_data)
        self.btn_fetch_bins = QPushButton("Fetch Bins")
        self.btn_fetch_bins.setToolTip("Request the per-direction spatial bin snapshots and "
                                       "verification residual DFTs from the firmware for the "
                                       "current profile. Required before Download/Copy can "
                                       "include bin data.")
        self.btn_fetch_bins.clicked.connect(self._on_fetch_bins)
        btn_row.addWidget(self.btn_fetch_bins)
        self.btn_load_data = QPushButton("Load from File")
        self.btn_load_data.setToolTip("Restore CW, CCW and DFT harmonics from a previously saved file")
        self.btn_load_data.clicked.connect(self._on_load_data)
        btn_row.addWidget(self.btn_load_data)
        self.btn_clear_cached = QPushButton("Clear Cached")
        self.btn_clear_cached.setToolTip("Clear all locally cached CW/CCW/DFT harmonic data")
        self.btn_clear_cached.clicked.connect(self._on_clear_cached)
        btn_row.addWidget(self.btn_clear_cached)
        self.btn_apply_fw = QPushButton("Apply to Firmware")
        self.btn_apply_fw.setToolTip("Send the edited harmonic magnitudes to the firmware "
                                      "so they take effect on the motor")
        self.btn_apply_fw.setStyleSheet("QPushButton { font-weight: bold; color: #27ae60; }")
        self.btn_apply_fw.clicked.connect(self._on_apply_to_firmware)
        btn_row.addWidget(self.btn_apply_fw)
        btn_row.addStretch(1)
        form.addRow("", btn_row)

        # CW and CCW direction overlay series
        self._dir_cw_series = QLineSeries()
        self._dir_cw_series.setName("CW Direction")
        self._dir_cw_series.setColor(QColor("red"))
        self._dir_cw_series.setOpacity(0.5)
        self.chart_wave.addSeries(self._dir_cw_series)
        self._dir_cw_series.attachAxis(self.axisX_wave)
        self._dir_cw_series.attachAxis(self.axisY_wave)
        self._dir_cw_series.hide()

        self._dir_ccw_series = QLineSeries()
        self._dir_ccw_series.setName("CCW Direction")
        self._dir_ccw_series.setColor(QColor("dodgerblue"))
        self._dir_ccw_series.setOpacity(0.5)
        self.chart_wave.addSeries(self._dir_ccw_series)
        self._dir_ccw_series.attachAxis(self.axisX_wave)
        self._dir_ccw_series.attachAxis(self.axisY_wave)
        self._dir_ccw_series.hide()

        # Fix chart backgrounds to match application palette
        palette_color = QApplication.instance().palette().window().color()
        self.chart_wave.setBackgroundBrush(palette_color)
        self.chart_bars.setBackgroundBrush(palette_color)

        self._rebuild_magnitude_editors()

    def _on_view_changed(self, idx):
        if idx == 0:
            self.view_wave.show()
            self.view_bars.hide()
        else:
            self.view_wave.hide()
            self.view_bars.show()
            self._rebuild_bar_chart()

    def _on_profile_changed(self, val):
        # Request harmonic data for the selected RPM profile from the firmware.
        # Firmware uses adr 0=profile1, 1=profile2, 2=profile3, etc.
        # self.spin_rpm_profile values are 1-based.
        if val >= 1:
            adr = val - 1
            self._current_profile = val

            # Use cached data if already loaded from a previous calibration broadcast
            cached = self.tmc_ui.cogging_harmonics_data_profiles.get(val, [])
            if cached:
                self.tmc_ui.cogging_harmonics_data = list(cached)
                self._profile_data_loaded = True
                self.label_profile_info.setText(f"Profile #{val}: cached")
            elif val > 1:
                # Profiles 2+ don't have per-profile shaping data (only RPM1 does).
                # Display Profile 1's combined graph instead.
                p1_data = self.tmc_ui.cogging_harmonics_data_profiles.get(1, [])
                self.tmc_ui.cogging_harmonics_data = list(p1_data)
                self._profile_data_loaded = bool(p1_data)
                self.label_profile_info.setText(f"Profile #{val}: showing Profile 1 combined"
                    + (" (cached)" if p1_data else " (not loaded)"))
            else:
                self._profile_data_loaded = False
                # Use the persistent callback with _pending_harmonics_adr routing
                self.tmc_ui._pending_harmonics_adr = adr
                self.tmc_ui.send_command("tmc", "coggingHarmonics", self.axis, '?', adr=adr)
                self.label_profile_info.setText(f"Profile #{val}: loading...")
            # Also fetch the RPM target for this profile
            self.tmc_ui.get_value_async("tmc", "coggingCalibRPM",
                                        lambda v, p=val: self._on_rpm_target_received(p, v),
                                        self.axis, int, adr=adr)
        else:
            self._current_profile = 1
        # Refresh CW/CCW direction graphs for the newly selected profile
        if self.chk_show_dir_graphs.isChecked():
            self._on_show_dir_graphs_toggled(True)
        self._y_range_locked = False
        self.redraw()
        self._rebuild_bar_chart()
        self._rebuild_magnitude_editors()

    def _on_profile_harmonics_loaded(self, data):
        """Callback when firmware returns harmonics for a specific RPM profile."""
        try:
            harmonics = []
            if not data or data == "0:0:0":
                pass  # empty profile
            else:
                for item in str(data).split(","):
                    parts = item.split(":")
                    if len(parts) == 3:
                        order = int(parts[0])
                        amp = float(parts[1])
                        phase = float(parts[2]) / 1000.0
                        if order > 0 or amp > 0:
                            harmonics.append((order, amp, phase))

            # Store per-profile
            self.tmc_ui.cogging_harmonics_data_profiles[self._current_profile] = harmonics
            # Swap the active harmonic data to the selected profile
            self.tmc_ui.cogging_harmonics_data = harmonics
            self._profile_data_loaded = True
        except Exception:
            pass
        self._y_range_locked = False
        self.redraw()
        self._rebuild_bar_chart()
        self._rebuild_magnitude_editors()

    def _on_rpm_target_received(self, profile_idx, val):
        """Callback for coggingCalibRPM query — updates per-profile RPM display."""
        try:
            rpm = int(val) // 10  # RPM*10 from firmware
            if rpm > 0:
                self.tmc_ui.cogging_rpm_targets[profile_idx] = rpm
                if profile_idx == self._current_profile and rpm > 0:
                    self.label_profile_info.setText(f"Profile #{profile_idx}: measured at {rpm} RPM")
        except Exception:
            pass

    def _on_show_dir_graphs_toggled(self, checked):
        """Show CW and CCW raw direction graphs from _cw_harmonics_profiles."""
        self._dir_cw_series.clear()
        self._dir_ccw_series.clear()
        if not checked:
            self._dir_cw_series.hide()
            self._dir_ccw_series.hide()
            self.redraw()
            return

        # Pull CW/CCW data for the current profile
        cw = self.tmc_ui._cw_harmonics_profiles.get(self._current_profile, [])
        ccw = self.tmc_ui._ccw_harmonics_profiles.get(self._current_profile, [])

        N = 361  # duplicate 0° at 360° so the chart wraps cleanly
        if cw:
            self._dir_cw_series.show()
            vals = [0.0] * N
            for order, amp, phase_rad in cw:
                try:
                    order = int(order)
                    amp = float(amp)
                    phase_rad = float(phase_rad)
                except Exception:
                    continue
                if amp <= 0:
                    continue
                for i in range(N):
                    theta = (i / 360.0) * 2.0 * math.pi
                    vals[i] += amp * math.sin(order * theta + phase_rad)
            for i in range(N):
                self._dir_cw_series.append(float(i), vals[i])
        else:
            self._dir_cw_series.hide()

        if ccw:
            self._dir_ccw_series.show()
            vals = [0.0] * N
            for order, amp, phase_rad in ccw:
                try:
                    order = int(order)
                    amp = float(amp)
                    phase_rad = float(phase_rad)
                except Exception:
                    continue
                if amp <= 0:
                    continue
                for i in range(N):
                    theta = (i / 360.0) * 2.0 * math.pi
                    vals[i] += amp * math.sin(order * theta + phase_rad)
            for i in range(N):
                self._dir_ccw_series.append(float(i), vals[i])
        else:
            self._dir_ccw_series.hide()

        self.redraw()

    def updateHarmonicPosition(self):
        """Update the live position dot, vertical line, and angle readout on the waveform chart."""
        pos_deg = self.tmc_ui.cogging_position * 360.0
        torque = getattr(self.tmc_ui, 'cogging_measured_torque', 0)
        y_min = self.axisY_wave.min()
        y_max = self.axisY_wave.max()
        self.scatter_pos.clear()
        self.scatter_pos.append(pos_deg, torque)
        self.line_vmarker.clear()
        self.line_vmarker.append(pos_deg, y_min)
        self.line_vmarker.append(pos_deg, y_max)
        if hasattr(self, 'label_angle'):
            self.label_angle.setText(f"Angle: {pos_deg:.1f}°")

    def _build_data_text(self):
        """Build a multi-line text report of CW, CCW, DFT harmonics, spatial bins,
        and verification residual DFTs/bins for the current profile."""
        lines = []
        idx = self._current_profile
        lines.append(f"RPM Profile #{idx}")
        rpm = self.tmc_ui.cogging_rpm_targets.get(idx, 0)
        lines.append(f"Measured RPM: {rpm}")
        lines.append("")

        cw = self.tmc_ui._cw_harmonics_profiles.get(idx) or self.tmc_ui.cw_raw_harmonics
        ccw = self.tmc_ui._ccw_harmonics_profiles.get(idx) or self.tmc_ui.ccw_raw_harmonics
        dft = self.tmc_ui.cogging_harmonics_data_profiles.get(idx) or self.tmc_ui.cogging_harmonics_data
        cw_bins = self.tmc_ui._cw_bins_profiles.get(idx)
        ccw_bins = self.tmc_ui._ccw_bins_profiles.get(idx)
        ver_cw_bins = self.tmc_ui._ver_cw_bins_profiles.get(idx)
        ver_ccw_bins = self.tmc_ui._ver_ccw_bins_profiles.get(idx)
        ver_cw_harm = self.tmc_ui._ver_cw_harm_profiles.get(idx)
        ver_ccw_harm = self.tmc_ui._ver_ccw_harm_profiles.get(idx)

        lines.append("--- CW Direction Harmonics ---")
        if cw:
            lines.append("order : amplitude : phase_rad")
            for order, amp, phase in sorted(cw, key=lambda x: x[0]):
                lines.append(f"{order} : {amp:.1f} : {phase:.4f}")
        else:
            lines.append("(no data)")

        lines.append("")
        lines.append("--- CCW Direction Harmonics ---")
        if ccw:
            lines.append("order : amplitude : phase_rad")
            for order, amp, phase in sorted(ccw, key=lambda x: x[0]):
                lines.append(f"{order} : {amp:.1f} : {phase:.4f}")
        else:
            lines.append("(no data)")

        lines.append("")
        lines.append("--- Combined DFT Harmonics ---")
        if dft:
            lines.append("order : amplitude : phase_rad")
            for order, amp, phase in sorted(dft, key=lambda x: x[0]):
                lines.append(f"{order} : {amp:.1f} : {phase:.4f}")
        else:
            lines.append("(no data)")

        # Spatial bin snapshots (per-bin mean iq the DFT operated on).
        # Bins are emitted as 'index:value' pairs on one line each so the file
        # stays diff-friendly and easy to parse back. 720 lines per direction.
        def _emit_bins(name, bins):
            lines.append("")
            lines.append(f"--- {name} ---")
            if bins:
                lines.append("bin_index : mean_iq")
                for i, v in enumerate(bins):
                    # Skip trailing zeros only if the whole tail is zero (compactness),
                    # but emit at least the first 360 to keep shape visible.
                    lines.append(f"{i} : {v:.1f}")
            else:
                lines.append("(no data)")

        _emit_bins("CW Spatial Bins (mean iq per 0.5 deg)", cw_bins)
        _emit_bins("CCW Spatial Bins (mean iq per 0.5 deg)", ccw_bins)

        # Verification residual (with feedforward active)
        lines.append("")
        lines.append("--- Verification CW Residual DFT ---")
        if ver_cw_harm:
            lines.append("order : amplitude : phase_rad")
            for order, amp, phase in sorted(ver_cw_harm, key=lambda x: x[0]):
                lines.append(f"{order} : {amp:.1f} : {phase:.4f}")
        else:
            lines.append("(no data)")

        lines.append("")
        lines.append("--- Verification CCW Residual DFT ---")
        if ver_ccw_harm:
            lines.append("order : amplitude : phase_rad")
            for order, amp, phase in sorted(ver_ccw_harm, key=lambda x: x[0]):
                lines.append(f"{order} : {amp:.1f} : {phase:.4f}")
        else:
            lines.append("(no data)")

        _emit_bins("Verification CW Residual Bins (mean iq per 0.5 deg)", ver_cw_bins)
        _emit_bins("Verification CCW Residual Bins (mean iq per 0.5 deg)", ver_ccw_bins)

        return "\n".join(lines)

    def _on_download_data(self):
        """Save the current profile's CW, CCW and DFT harmonic data to a text file."""
        text = self._build_data_text()
        fname, _ = QFileDialog.getSaveFileName(
            self, "Save CW/CCW Harmonic Data",
            f"profile_{self._current_profile}_cwccw.txt",
            "Text Files (*.txt);;All Files (*)"
        )
        if fname:
            try:
                with open(fname, "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError as e:
                QMessageBox.warning(self, "Save Error", f"Could not save file:\n{e}")

    def _on_copy_data(self):
        """Copy the current profile's CW, CCW and DFT harmonic data to the clipboard."""
        text = self._build_data_text()
        QApplication.clipboard().setText(text)

    def _on_fetch_bins(self):
        """Pull all bin datasets from the firmware for the current profile.
        The firmware only keeps the LAST calibrated profile's bins, so this
        should be called right after calibration completes and before switching
        profiles."""
        self.tmc_ui.requestCoggingBins(self._current_profile)
        self.tmc_ui.main.log(f"Requested cogging bins for profile {self._current_profile}")

    def _on_load_data(self):
        """Restore CW, CCW and DFT harmonics from a previously saved text file.
        Parses the same format produced by Download CW/CCW Data (one profile per file)."""
        fname, _ = QFileDialog.getOpenFileName(
            self, "Load CW/CCW Harmonic Data",
            "", "Text Files (*.txt);;All Files (*)"
        )
        if not fname:
            return
        try:
            with open(fname, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            QMessageBox.warning(self, "Load Error", f"Could not read file:\n{e}")
            return

        # Parse the file
        import re
        cw_data = []
        ccw_data = []
        dft_data = []
        current_section = None
        profile_idx = None
        rpm = 0

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # RPM Profile header
            m_rpm = re.match(r"^RPM Profile #(\d+)", line)
            if m_rpm:
                profile_idx = int(m_rpm.group(1))
                self._current_profile = profile_idx
                self.spin_rpm_profile.setValue(max(1, min(profile_idx, self.spin_rpm_profile.maximum())))
                continue
            m_meas = re.match(r"^Measured RPM:\s*(\d+)", line)
            if m_meas:
                rpm = int(m_meas.group(1))
                self.tmc_ui.cogging_rpm_targets[profile_idx or 1] = rpm
                continue
            # Section markers
            if line.startswith("--- CW Direction Harmonics ---"):
                current_section = "CW"
                continue
            if line.startswith("--- CCW Direction Harmonics ---"):
                current_section = "CCW"
                continue
            if line.startswith("--- Combined DFT Harmonics ---"):
                current_section = "DFT"
                continue
            if line.startswith("order :"):
                continue
            if line == "(no data)":
                current_section = None
                continue
            # Parse harmonic line: "order : amplitude : phase_rad"
            parts = line.split(":")
            if len(parts) == 3 and current_section:
                try:
                    order = int(parts[0].strip())
                    amp = float(parts[1].strip())
                    phase_rad = float(parts[2].strip())
                    if amp > 0.0:
                        entry = (order, amp, phase_rad)
                        if current_section == "CW":
                            cw_data.append(entry)
                        elif current_section == "CCW":
                            ccw_data.append(entry)
                        elif current_section == "DFT":
                            dft_data.append(entry)
                except ValueError:
                    continue

        if profile_idx is None:
            profile_idx = self._current_profile

        # Store into the per-profile caches
        if cw_data:
            self.tmc_ui._cw_harmonics_profiles[profile_idx] = cw_data
            self.tmc_ui.cw_raw_harmonics = cw_data
        if ccw_data:
            self.tmc_ui._ccw_harmonics_profiles[profile_idx] = ccw_data
            self.tmc_ui.ccw_raw_harmonics = ccw_data
        if dft_data:
            self.tmc_ui.cogging_harmonics_data_profiles[profile_idx] = dft_data
            self.tmc_ui.cogging_harmonics_data = dft_data
            self._profile_data_loaded = True
        elif profile_idx > 1:
            # Fall back to profile 1's combined if no DFT in file
            p1_data = self.tmc_ui.cogging_harmonics_data_profiles.get(1, [])
            self.tmc_ui.cogging_harmonics_data = list(p1_data)
        self._profile_data_loaded = True
        self.label_profile_info.setText(f"Profile #{profile_idx}: loaded from file"
            + (f" ({rpm} RPM)" if rpm > 0 else ""))

        # Refresh all UI
        self.tmc_ui.rebuildCwCcwWaveforms()
        self.tmc_ui.rebuildProfileWaveform()
        self._y_range_locked = False
        self.redraw()
        self._rebuild_bar_chart()
        self._rebuild_magnitude_editors()

    def _on_apply_to_firmware(self):
        """Send the edited harmonic magnitudes to the firmware.
        Uses coggingH3 setat: adr=3 clears table, adr=4 sets amplitude+order,
        adr=5 sets phase."""
        harms = getattr(self.tmc_ui, "cogging_harmonics_data", [])
        if not harms:
            return

        # Clear the base harmonic table first
        self.tmc_ui.send_value("tmc", "coggingH3", val=0, adr=3, instance=self.axis)

        # Send each harmonic entry
        for slot, h in enumerate(harms):
            try:
                order = int(h[0])
                amp = float(h[1])
                phase_rad = float(h[2])
            except Exception:
                continue
            if order <= 0 or amp <= 0:
                continue
            if slot >= 20:  # COGGING_HARMONICS_COUNT
                break

            amp_int = int(round(amp))
            phase_mrad = int(round(phase_rad * 1000.0))

            # adr=4: slot<<24 | order<<16 | amplitude
            set_val = (slot << 24) | ((order & 0xFF) << 16) | (amp_int & 0xFFFF)
            self.tmc_ui.send_value("tmc", "coggingH3", val=set_val, adr=4, instance=self.axis)

            # adr=5: slot<<24 | phase_mrad
            phase_val = (slot << 24) | (phase_mrad & 0xFFFF)
            self.tmc_ui.send_value("tmc", "coggingH3", val=phase_val, adr=5, instance=self.axis)

        # Re-enable cogging so the table takes effect
        self.tmc_ui.send_value("tmc", "cogging", val=1, instance=self.axis)

    def _on_clear_cached(self):
        """Clear all locally cached CW/CCW/DFT harmonic data."""
        self.tmc_ui._cw_harmonics_profiles.clear()
        self.tmc_ui._ccw_harmonics_profiles.clear()
        self.tmc_ui.cogging_harmonics_data_profiles.clear()
        self.tmc_ui.cw_raw_harmonics.clear()
        self.tmc_ui.ccw_raw_harmonics.clear()
        self.tmc_ui.cogging_harmonics_data.clear()
        self.tmc_ui.cogging_rpm_targets.clear()
        self.tmc_ui.clearCoggingGraph()
        self._profile_data_loaded = False
        self.label_profile_info.setText("Cached data cleared")
        self.redraw()
        self._rebuild_bar_chart()
        self._rebuild_magnitude_editors()

    def _rebuild_bar_chart(self):
        harms = getattr(self.tmc_ui, "cogging_harmonics_data", [])
        self.bar_set.remove(0, self.bar_set.count())
        self.bar_set.append(0.0)
        bars = [0.0] * 128
        for order, amp, _phase in harms:
            if 1 <= int(order) <= 128:
                bars[int(order) - 1] = float(amp)
        self.bar_set.append(bars)
        self.axisX_bars.setRange(1, 128)
        valid_data = [amp for _order, amp, _phase in harms if amp > 0]
        if valid_data:
            self.axisY_bars.setMax(max(10, max(valid_data)))
            self.axisY_bars.setMin(0)
        else:
            self.axisY_bars.setMin(0)
            self.axisY_bars.setMax(10)

    def _rebuild_magnitude_editors(self):
        """Rebuild per-harmonic magnitude spinboxes from current harmonic data."""
        # Clear existing
        for order_lbl, amp_spin in self._harm_spinboxes:
            try:
                order_lbl.deleteLater()
                amp_spin.deleteLater()
            except Exception:
                pass
        self._harm_spinboxes = []
        while self.harm_container_layout.count():
            item = self.harm_container_layout.takeAt(0)
            if item.widget():
                try:
                    item.widget().deleteLater()
                except Exception:
                    pass

        harms = getattr(self.tmc_ui, "cogging_harmonics_data", [])
        if not harms:
            no_data = QLabel("(no harmonic data loaded)")
            self.harm_container_layout.addWidget(no_data)
            return

        for order, amp, phase_rad in harms:
            try:
                order = int(order)
                amp = float(amp)
            except Exception:
                continue
            if amp <= 0 and order <= 0:
                continue

            row = QHBoxLayout()
            lbl = QLabel(f"H{order}")
            lbl.setFixedWidth(40)
            lbl.setStyleSheet("font-weight: bold;")
            row.addWidget(lbl)

            spin = QDoubleSpinBox()
            spin.setRange(0.0, 999999.0)
            spin.setDecimals(1)
            spin.setSingleStep(10.0)
            spin.setValue(amp)
            spin.valueChanged.connect(lambda v, o=order: self._on_magnitude_changed(o, v))
            row.addWidget(spin, 1)

            row_w = QWidget()
            row_w.setLayout(row)
            self.harm_container_layout.addWidget(row_w)
            self._harm_spinboxes.append((lbl, spin))

    def _on_magnitude_changed(self, order, val):
        """Called when a per-harmonic magnitude spinbox is edited."""
        harms = getattr(self.tmc_ui, "cogging_harmonics_data", [])
        for i, h in enumerate(harms):
            try:
                if int(h[0]) == order:
                    harms[i] = (order, float(val), float(h[2]))
                    break
            except Exception:
                continue
        self.redraw()

    def redraw(self):
        self.orig_series.clear()
        self.shaped_series.clear()
        self.shaped_series.setName("Edited")

        harms = getattr(self.tmc_ui, "cogging_harmonics_data", [])

        N = 360
        edited = [0.0] * N
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
                edited[i] += amp * math.sin(order * theta + phase_rad)

        if not have_data and not self.chk_show_dir_graphs.isChecked():
            self.axisY_wave.setRange(-1.0, 1.0)
            return

        # Only auto-scale Y-axis on initial data load, not on manual magnitude edits
        if not getattr(self, '_y_range_locked', False):
            y_min = min(edited) if edited else -1.0
            y_max = max(edited) if edited else 1.0

            if self.chk_show_dir_graphs.isChecked():
                for s in (self._dir_cw_series, self._dir_ccw_series):
                    if s.isVisible():
                        pts = s.points()
                        if pts:
                            vals = [p.y() for p in pts]
                            y_min = min(y_min, min(vals))
                            y_max = max(y_max, max(vals))

            if y_max - y_min < 1e-6:
                y_max = y_min + 1.0
            pad = (y_max - y_min) * 0.1
            self.axisY_wave.setRange(y_min - pad, y_max + pad)
            self._y_range_locked = True

        for i in range(N):
            self.shaped_series.append(float(i), edited[i])

        self._rebuild_bar_chart()
        self.updateHarmonicPosition()


class CoggingCalibrationTab(QWidget):
    """Cogging Calibration tab — Start calibration button, auto PID tune checkbox,
    and multi-RPM profile settings using firmware commands.

    Firmware commands used:
      - coggingCalibCount: get/set number of profiles (1-5)
      - coggingCalibRPM: getat/setat RPM target per profile (adr=profile_idx, val=RPM*10)
      - coggingCalibIters: getat/setat iterations per profile (adr=profile_idx, val=iterations)
      - coggingCalibPidP/I/D: getat/setat manual PID per profile (adr=profile_idx, val=PID*1000)
      - coggingCalibAutoPid: get/set auto PID tune flag (1=auto, 0=manual)
    """

    MAX_RPM_PROFILES = 5

    def __init__(self, tmc_ui, axis):
        super().__init__()
        self.tmc_ui = tmc_ui
        self.axis = axis
        self.rpm_profile_widgets = []  # [(rpm_spin, iters_spin, p_spin, i_spin, d_spin), ...]
        self._loading = False

        layout = QVBoxLayout(self)

        # ---- Calibration button ----
        self.btn_start = QPushButton("Start Cogging Calibration")
        self.btn_start.setToolTip("Begin the cogging detection / calibration routine")
        self.btn_start.clicked.connect(self._on_start_calibration)
        layout.addWidget(self.btn_start)

        # ---- Auto Velocity PID Tune checkbox ----
        self.chk_auto_pid = QCheckBox("Auto Velocity PID Tune")
        self.chk_auto_pid.setChecked(True)
        self.chk_auto_pid.setToolTip("When ticked, velocity PID is auto-tuned during calibration. "
                                      "Untick to manually set velocity PID per profile below.")
        self.chk_auto_pid.toggled.connect(self._on_auto_pid_toggled)
        layout.addWidget(self.chk_auto_pid)

        # Inertia Acceleration Correction checkbox
        self.chk_inertia_corr = QCheckBox("Inertia Acceleration Correction")
        self.chk_inertia_corr.setChecked(False)
        self.chk_inertia_corr.setToolTip("Subtract acceleration torque (J·α) from DFT measurement "
                                          "to remove inertial bias from cogging table")
        self.chk_inertia_corr.toggled.connect(self._on_inertia_corr_toggled)
        layout.addWidget(self.chk_inertia_corr)

        # Friction Feedforward checkbox
        self.chk_friction_ff = QCheckBox("Friction Feedforward")
        self.chk_friction_ff.setChecked(False)
        self.chk_friction_ff.setToolTip("Apply friction feedforward torque (B·ω) during calibration "
                                         "to reduce friction-induced DFT bias")
        self.chk_friction_ff.toggled.connect(self._on_friction_ff_toggled)
        layout.addWidget(self.chk_friction_ff)

        # ---- Multi-RPM calibration settings ----
        rpm_group = QGroupBox("Multi-RPM Calibration Settings")
        rpm_vbox = QVBoxLayout(rpm_group)

        num_row = QHBoxLayout()
        num_row.addWidget(QLabel("Number of RPM profiles (firmware):"))
        self.lbl_num_rpms = QLabel("3")
        self.lbl_num_rpms.setStyleSheet("font-weight: bold; font-size: 14px;")
        num_row.addWidget(self.lbl_num_rpms)
        num_row.addStretch(1)
        rpm_vbox.addLayout(num_row)
        self._num_rpms = 3  # read-only, queried from firmware

        self.rpm_scroll = QScrollArea()
        self.rpm_scroll.setWidgetResizable(True)
        self.rpm_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.rpm_container = QWidget()
        self.rpm_container_layout = QVBoxLayout(self.rpm_container)
        self.rpm_container_layout.setContentsMargins(0, 0, 0, 0)
        self.rpm_scroll.setWidget(self.rpm_container)
        self.rpm_scroll.setMinimumHeight(60)
        self.rpm_scroll.setMaximumHeight(400)
        rpm_vbox.addWidget(self.rpm_scroll)

        layout.addWidget(rpm_group)
        layout.addStretch(1)

        self._rebuild_rpm_profiles()
        self._update_enabled_state()
        self._load_all_from_firmware()
        # Listen for calibration state changes to re-enable the button
        self._cal_state_timer = QTimer(self)
        self._cal_state_timer.setInterval(500)
        self._cal_state_timer.timeout.connect(self._poll_cal_state)
        self._cal_state_timer.start()

    def _on_auto_pid_toggled(self, checked):
        """When auto-tune is ticked, grey out per-profile PID spinboxes and send to firmware."""
        for rpm_spin, iters_spin, p_spin, i_spin, d_spin in self.rpm_profile_widgets:
            p_spin.setEnabled(not checked)
            i_spin.setEnabled(not checked)
            d_spin.setEnabled(not checked)
        self.tmc_ui.send_value("tmc", "coggingCalibAutoPid", val=1 if checked else 0, instance=self.axis)
        if not checked:
            # Send current manual PID values to firmware when switching to manual
            for i, (rpm_spin, iters_spin, p_spin, i_spin, d_spin) in enumerate(self.rpm_profile_widgets):
                self.tmc_ui.send_value("tmc", "coggingCalibPidP", adr=i, val=p_spin.value(), instance=self.axis)
                self.tmc_ui.send_value("tmc", "coggingCalibPidI", adr=i, val=i_spin.value(), instance=self.axis)
                self.tmc_ui.send_value("tmc", "coggingCalibPidD", adr=i, val=d_spin.value(), instance=self.axis)

    def _on_num_rpms_received(self, val):
        """Called when firmware reports the number of profiles (read-only)."""
        try:
            v = int(val)
            if 1 <= v <= self.MAX_RPM_PROFILES:
                self._num_rpms = v
                # Only increase, never decrease — calibration broadcasts may
                # have set a larger value for scale-curve RPM profiles.
                if v > self.tmc_ui.cogging_profile_count:
                    self.tmc_ui.cogging_profile_count = v
                self.lbl_num_rpms.setText(str(v))
                self._rebuild_rpm_profiles()
                # Update harmonic editor spinbox range if the dialog is already open
                if hasattr(self.tmc_ui, '_scale_dlg') and self.tmc_ui._scale_dlg is not None:
                    self.tmc_ui._scale_dlg.h3_tab.spin_rpm_profile.setRange(1, v)
        except Exception:
            pass

    def _rebuild_rpm_profiles(self):
        # Clear existing
        for group in self.rpm_profile_widgets:
            for w in group:
                try:
                    w.deleteLater()
                except Exception:
                    pass
        while self.rpm_container_layout.count():
            item = self.rpm_container_layout.takeAt(0)
            if item.widget():
                try:
                    item.widget().deleteLater()
                except Exception:
                    pass
            elif item.layout():
                self._clear_layout(item.layout())
        self.rpm_profile_widgets = []

        num = self._num_rpms
        auto_pid = self.chk_auto_pid.isChecked()

        # Use horizontal layout so multiple profiles can fit side by side
        profiles_row = QHBoxLayout()
        self.rpm_container_layout.addLayout(profiles_row)

        for i in range(num):
            grp = QGroupBox(f"RPM#{i+1}")
            form = QFormLayout(grp)
            form.setContentsMargins(3, 3, 3, 3)

            rpm_spin = QSpinBox()
            rpm_spin.setRange(1, 500)
            rpm_spin.setSuffix(" RPM")
            rpm_spin.setValue(3 if i == 0 else (30 if i == 1 else 100))
            rpm_spin.setToolTip(f"Target RPM for RPM#{i+1}")
            rpm_spin.setMaximumWidth(100)
            idx = i
            rpm_spin.valueChanged.connect(lambda v, i2=idx: self._on_rpm_changed(i2, v))
            form.addRow("RPM:", rpm_spin)

            iters_spin = QSpinBox()
            iters_spin.setRange(1, 100)
            iters_spin.setValue(3)
            iters_spin.setToolTip(f"DFT iterations for RPM#{i+1}")
            iters_spin.setMaximumWidth(70)
            iters_spin.valueChanged.connect(lambda v, i2=idx: self._on_iters_changed(i2, v))
            form.addRow("Iters:", iters_spin)

            p_spin = QSpinBox()
            p_spin.setRange(0, 9999999)
            p_spin.setSingleStep(100)
            p_spin.setValue(10000)
            p_spin.setEnabled(not auto_pid)
            p_spin.setMaximumWidth(100)
            p_spin.setToolTip(f"Manual P gain for RPM#{i+1}")
            p_spin.valueChanged.connect(lambda v, i2=idx: self._on_profile_pid_changed(i2, "coggingCalibPidP", v))
            form.addRow("P:", p_spin)

            i_spin = QSpinBox()
            i_spin.setRange(0, 9999999)
            i_spin.setSingleStep(10)
            i_spin.setValue(0)
            i_spin.setEnabled(not auto_pid)
            i_spin.setMaximumWidth(100)
            i_spin.setToolTip(f"Manual I gain for RPM#{i+1}")
            i_spin.valueChanged.connect(lambda v, i2=idx: self._on_profile_pid_changed(i2, "coggingCalibPidI", v))
            form.addRow("I:", i_spin)

            d_spin = QSpinBox()
            d_spin.setRange(0, 9999999)
            d_spin.setSingleStep(10)
            d_spin.setValue(0)
            d_spin.setEnabled(not auto_pid)
            d_spin.setMaximumWidth(100)
            d_spin.setToolTip(f"Manual D gain for RPM#{i+1}")
            d_spin.valueChanged.connect(lambda v, i2=idx: self._on_profile_pid_changed(i2, "coggingCalibPidD", v))
            form.addRow("D:", d_spin)

            profiles_row.addWidget(grp)
            self.rpm_profile_widgets.append((rpm_spin, iters_spin, p_spin, i_spin, d_spin))

        profiles_row.addStretch(1)

    def _on_rpm_changed(self, profile_idx, val):
        if self._loading:
            return
        self.tmc_ui.send_value("tmc", "coggingCalibRPM", adr=profile_idx, val=val * 10, instance=self.axis)

    def _on_iters_changed(self, profile_idx, val):
        if self._loading:
            return
        self.tmc_ui.send_value("tmc", "coggingCalibIters", adr=profile_idx, val=val, instance=self.axis)

    def _on_inertia_corr_toggled(self, checked):
        self.tmc_ui.send_value("tmc", "coggingCalibInertiaCorr", val=1 if checked else 0, instance=self.axis)

    def _on_friction_ff_toggled(self, checked):
        self.tmc_ui.send_value("tmc", "coggingCalibFrictionFF", val=1 if checked else 0, instance=self.axis)

    def _friction_ff_cb(self, val):
        """Callback for coggingCalibFrictionFF query."""
        try:
            self._loading = True
            checked = int(val) != 0
            self.chk_friction_ff.setChecked(checked)
            self._loading = False
        except Exception:
            self._loading = False

    def _on_profile_pid_changed(self, profile_idx, cmd, val):
        if self._loading or self.chk_auto_pid.isChecked():
            return
        self.tmc_ui.send_value("tmc", cmd, adr=profile_idx, val=val, instance=self.axis)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                try:
                    item.widget().deleteLater()
                except Exception:
                    pass
            elif item.layout():
                self._clear_layout(item.layout())

    def _load_all_from_firmware(self):
        """Query firmware for all multi-RPM calibration profile settings."""
        # Query profile count (read-only)
        self.tmc_ui.get_value_async("tmc", "coggingCalibCount", self._on_num_rpms_received, self.axis, int)
        # Query auto PID flag
        self.tmc_ui.get_value_async("tmc", "coggingCalibAutoPid", self._auto_pid_cb, self.axis, int)
        # Query friction feedforward flag
        self.tmc_ui.get_value_async("tmc", "coggingCalibFrictionFF", self._friction_ff_cb, self.axis, int)
        # Query RPM targets and iterations and PIDs for each profile
        for i in range(self.MAX_RPM_PROFILES):
            idx = i
            self.tmc_ui.get_value_async("tmc", "coggingCalibRPM", lambda v, i2=idx: self._rpm_cb(i2, v), self.axis, int, adr=i)
            self.tmc_ui.get_value_async("tmc", "coggingCalibIters", lambda v, i2=idx: self._iters_cb(i2, v), self.axis, int, adr=i)
            self.tmc_ui.get_value_async("tmc", "coggingCalibPidP", lambda v, i2=idx: self._pid_cb(i2, "P", v), self.axis, int, adr=i)
            self.tmc_ui.get_value_async("tmc", "coggingCalibPidI", lambda v, i2=idx: self._pid_cb(i2, "I", v), self.axis, int, adr=i)
            self.tmc_ui.get_value_async("tmc", "coggingCalibPidD", lambda v, i2=idx: self._pid_cb(i2, "D", v), self.axis, int, adr=i)

    # _count_cb replaced by _on_num_rpms_received

    def _auto_pid_cb(self, val):
        try:
            self._loading = True
            checked = int(val) != 0
            self.chk_auto_pid.setChecked(checked)
            self._loading = False
        except Exception:
            self._loading = False

    def _rpm_cb(self, idx, val):
        try:
            v = int(val) // 10  # RPM*10 from firmware
            if idx < len(self.rpm_profile_widgets):
                rpm_spin, _, _, _, _ = self.rpm_profile_widgets[idx]
                rpm_spin.blockSignals(True)
                rpm_spin.setValue(v)
                rpm_spin.blockSignals(False)
        except Exception:
            pass

    def _iters_cb(self, idx, val):
        try:
            v = int(val)
            if idx < len(self.rpm_profile_widgets):
                _, iters_spin, _, _, _ = self.rpm_profile_widgets[idx]
                iters_spin.blockSignals(True)
                iters_spin.setValue(v)
                iters_spin.blockSignals(False)
        except Exception:
            pass

    def _pid_cb(self, idx, pid_type, val):
        try:
            v = int(val)
            if idx < len(self.rpm_profile_widgets):
                _, _, p_spin, i_spin, d_spin = self.rpm_profile_widgets[idx]
                target = p_spin if pid_type == "P" else (i_spin if pid_type == "I" else d_spin)
                target.blockSignals(True)
                target.setValue(v)
                target.blockSignals(False)
        except Exception:
            pass

    def _update_enabled_state(self):
        calibrating = getattr(self.tmc_ui, 'cogging_calibrating', False)
        supported = getattr(self.tmc_ui, 'cogging_supported', False)
        self.btn_start.setEnabled(supported and not calibrating)

    def _poll_cal_state(self):
        """Periodically check if calibration has finished so we can re-enable the button."""
        calibrating = getattr(self.tmc_ui, 'cogging_calibrating', False)
        if not calibrating:
            self._update_enabled_state()

    def _on_start_calibration(self):
        self.tmc_ui.coggingDetection()
        self._update_enabled_state()
        QTimer.singleShot(500, self._update_enabled_state)

    def sync_pid_values(self):
        """Called when dialog opens — re-fetch current values from firmware."""
        self._load_all_from_firmware()

    def shutdown(self):
        """Stop the poll timer when the dialog is closed."""
        if hasattr(self, '_cal_state_timer') and self._cal_state_timer is not None:
            self._cal_state_timer.stop()


class ScalePhaseAdvanceDialog(QDialog):
    """Tabbed editor for Cogging Calibration.

    Non-modal — you can interact with the main OpenFFBoard window while open.
    Supports full-screen snapping when dragged to screen edges.
    """
    def __init__(self, tmc_ui, axis):
        super().__init__(tmc_ui)
        self.tmc_ui = tmc_ui
        self.axis = axis
        self.setWindowTitle("Cogging Calibration")
        self.setMinimumSize(720, 560)
        flags = self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint
        flags = flags & ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Cogging Calibration tab (FIRST tab)
        self.cogging_cal_tab = CoggingCalibrationTab(tmc_ui, axis)
        self.tabs.addTab(self.cogging_cal_tab, "Cogging Calibration")

        # Scale Curve tab
        self.scale_tab = CurveEditorTab(
            tmc_ui, axis, "scaleCurve", "Scale", 0.0, 10.0, 0.1, scale=1000.0, decimals=2)
        self.tabs.addTab(self.scale_tab, "Scale Curve")

        # Phase Advance tab
        self.phase_tab = CurveEditorTab(
            tmc_ui, axis, "phaseAdvCurve", "Phase Advance (deg)", -5.0, 15.0, 0.25, scale=100.0, decimals=2)
        self.tabs.addTab(self.phase_tab, "Phase Advance")

        # Harmonic Editor tab
        self.h3_tab = HarmShapingTab(tmc_ui, axis)
        self.tabs.addTab(self.h3_tab, "Harmonic Editor")

        # Live RPM dot polling (20ms)
        self.live_timer = QTimer(self)
        self.live_timer.setInterval(20)
        self.live_timer.timeout.connect(self._poll_live)
        self.current_rpm = 0.0

        self._load_curves()

    def showEvent(self, event):
        super().showEvent(event)
        self._load_curves()
        self.live_timer.start()
        self.cogging_cal_tab._update_enabled_state()

    def hideEvent(self, event):
        self.live_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.live_timer.stop()
        self.cogging_cal_tab.shutdown()
        super().closeEvent(event)

    def _load_curves(self):
        self.tmc_ui.register_callback("tmc", "scaleCurve", self._scale_curve_cb, self.axis, str, typechar='?', delete=True)
        self.tmc_ui.register_callback("tmc", "phaseAdvCurve", self._phase_curve_cb, self.axis, str, typechar='?', delete=True)
        self.tmc_ui.send_command("tmc", "scaleCurve", self.axis, '?')
        self.tmc_ui.send_command("tmc", "phaseAdvCurve", self.axis, '?')
        self.tmc_ui.send_command("tmc", "coggingHarmonics", self.axis, '?')
        QTimer.singleShot(300, self.h3_tab.redraw)
        QTimer.singleShot(350, self.h3_tab._rebuild_magnitude_editors)

    def _parse_curve(self, data, scale):
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
        self.current_rpm = abs(getattr(self.tmc_ui, 'vel_rpm', 0.0))
        self.scale_tab.set_live_rpm(self.current_rpm)
        self.phase_tab.set_live_rpm(self.current_rpm)
        self.h3_tab.updateHarmonicPosition()


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
        self.send_value("tmc","tmcHwType",self.combobox.currentData(),instance=self.axis)
        self.parent.init_ui()
    
    def typeCb(self,entries):
        entriesList = entries.split("\n")
        entriesList = [m.split(":") for m in entriesList if m]
        for m in entriesList:
            self.combobox.addItem(m[1],m[0])
        self.get_value_async("tmc","tmcHwType",lambda val : self.combobox.setCurrentIndex(self.combobox.findData(val)),self.axis,int)

    def readValues(self):
        self.get_value_async("tmc","tmcHwType",self.typeCb,self.axis,str,typechar='!')