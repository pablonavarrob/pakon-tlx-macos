#!/usr/bin/env python3
"""Name tables for TLB.dll's internal error reporter (RVA 0x1acd0).

Recovered from the id->name jump tables in the binary itself:
  classes   table 0x10019e18 (index = id-1)
  functions table 0x10019ed0 (index = id-1)
  errors    switch 0x10018eeb, range tables 0x1001a478 / 0x1001a4f0 /
            0x1001a710 / 0x1001a768 / 0x1001a7c0 / 0x1001a80c + singletons

The DLL logs "TLBERR cls=.. fn=.. ec=.. extra=.." and this puts names to it, so
the tables stay editable without rebuilding the shim.
"""

CLASSES = """Win32 Global CiApsMofInternal CiBuffer CiBufferFileSystem CiBufferHiRes
CiBufferImported CiBufferImportedHiRes CiBufferStrip CiCmdComm CiColorCorrection
CiColorCorrectionKodak CiConfig CiConfigColorKodak CiConfigDpi
CiConfigFixedPatternCorrection CiConfigLight CiConfigMain CiConfigMemory
CiConfigSave CiConfigScan CiConfigSimulator CiConfigTest CiCriticalSection
CiDLLDigitalIce CiDLLPakonIma CiDLLPakonMof CiDllRoot CiDxAndApsHole CiFirmware
CiImage CiLineWidth CiList CiMeteredSection CiPicture CiPicLoc CiScanner
CiScanSpeedRollingAverages CiStringUC CiThreadDataCorrections
CiThreadDataInitializeScanner CiThreadDataLongOps CiThreadDataSavePictures
CiThreadDataScanDriver CiThreadDataScanPictures CiTLAMain""".split()

FUNCS = """Constructor Destructor bAdjustDxPots AdvanceFilm AfterScan bAfterScan
iAfterScanFinished bAidGetMofFrameData bAidNewRoll bAidSetLeaderData bAnalyze
bApplyColorCircuit bApplyKodakColorCorrection bApplyColorAdjustments
ApsManualRetract bAssembleMofData bBeforeScan CalibrateAcquire
bCalibrateAcquireAndAverageLines CalibrateAdvanceFilm bCalibrateAfter
bCalibrateBefore CalibrateBegin CalibrateChange bCalibrateEnd
bCalibrateEndDataFlow bCalibrateFindCorrections bCalibrateFindDarkOffset
bCalibrateFindExposures bCalibrateFindLiteAndDark bCalibrateFindLedCurrent
bCalibrateFindLedCurrentSub bCalibrateFindLedDutyCycle
bCalibrateFindOpenGateExposures CalibrateFocus bCalibrateFindSmearAndFPC
bCalibrateFixedPatternBright bCalibrateFixedPatternDark
bCalibrateFixedPatternIrLag bCalibrateLEDs CalibratePutFilmPressureRollerPosition
CalibratePutFilterWheelPosition bCalibrateStartDataFlow bCalibrateWizard
bCheckRanges bClientCallBackLoop bClientFilesExist bClientMemoryBufferAdd
ClientMemoryBufferAdd bClientMemoryBufferDismissOldest
bClientMemoryBufferGetOldest bClientMemoryBufferDismissAll
ClientMemoryBufferDismissAll bColorAdjustDib8 UNNAMED_55 bColorAdjustTempImage
bColorAdjustLutAllocateAndFill bColorAdjustLutFill bPlanarToDib8
bPlanar16ToPlanar8 bConvertAndReadLut bLoadColNegDefault bLoadColRevClient
bLoadColRevDefault bLoadDefaultLut bLoadDefaultMatrix UNNAMED_67 bCopy bCreate
bCreate2DLut LoadLut1 bCreateDirectory bCreateEvent bNewRoll bCreateFileView
bCreateIndexList bRecycleRoll bDisplay bDriveMotorAdvanceFilm bDriveMotorStop
uiDriverPollPPB bDrvCcdAcquireControl bDrvCcdAquireControl bDrvApsFilmControl
bDrvApsReadMofData bDrvClose bDrvDxChangePots bDrvDxGetHardware
bDrvDxPutHardware bDrvDxStart bDrvDxStop bDrvFindPicController
bDrvGetByteArrayNL bDrvGetDevInfo bDrvGetHardwareStatusDx
bDrvGetHardwareStatusLamp bDrvGetHardwareStatusMotor bDrvGetHardwareStatusPicF
bDrvGetPpbDeviceReadyNL bDrvGetPpbGeneralStatus bDrvGetPpbHostStatusByte
bDrvGetPpbInfoBufferString bDrvGetPpbInterruptStatus bDrvGetRegisterByte
bDrvGetRegisterCcd bDrvGetRegisterWord iDrvGetStepperPosition bDrvMoveFilmGuides
bDrvMoveFilmPressureRollers bDrvMoveFilterWheel bDrvLampOff bDrvLampOn
bDrvMoveFocusSteppers bDrvOpen bDrvPacketExecuteNL bDrvPacketHandleErrorNL
bDrvPutCcdAtoDGains bDrvPutCcdAtoDOffsets bDrvPutCcdExposures
bDrvPutCcdFpgaControlReg bDrvPutCcdFpgaSettings bDrvPutCcdIrMode bDrvPutCommand
bDrvPutLampLevel bDrvPutLampLevelIr bDrvPutRegister bDrvPutRegisterByte
bDrvPutRegisterCcd bDrvPutRegisterWord bDrvReadScanLine bDrvResetFifos
bDrvResetHost bDrvSendTimeOutSet bDrvSendWord bDrvSetLed bDrvSetMotorVariables
bDrvSetScanVars bDrvSteppersStopped bDrvWritePacketNL bEEPromRead
bEEPromReadSection bEEPromSendVendReq bEEPromWrite bEEPromWriteSection
bEntryAndExitSensorCalibration bExerciseSteppers bExit bExtract bFakeDriver
bFileClose bFileOpenWrite bFilmFound FilmTrackTest bFilmTrackTest bFindDmin
bFindFirstAvailableRoll bFindSaveFileDimensions FinishedLoadingRaw
bFirmwareGetByteArrayNL bFirmwareGetProgramWord bFirmwareGetProgramWords8
bFirmwarePutProgramData bFirmwarePutProgramWord bFirmwareWritePacketNL
bFocusAbsPixDifRed bFocusDiag bFocusFillPredictorArray bFocusFindBestLine
bFocusFindPosition bFocusPositionFilm bFocusQuadRegress ForceCorrections
bFPCConfigAdd bFPCConfigAnalyze iFramePictures iFramePicturesSub
iFramingCreateOnesArray iFramingEstimatePlacement iFramingFindPictures
bFramingLookAtBeginning bFramingLookAtEnd bFreeColorCiruit FuncCorrections
FuncImportFromFile FuncInitializeScanner FuncSaveToClientMemory
FuncSaveToSharedMemory FuncSaveToDisk FuncScanPictures bGetAPSFrames
bGetBytesPerSector GetCalibrateEEProm GetCalibrateInfoColorMatrix
GetCalibrateInfoDpi GetCalibrateInfoLight bGetColorMatrix uiGetCorrections
bGetDXProductAndSpecifier bGetErrors bGetHiResImage bGetPicMofDataPacket
GetPictureInfoAid bGetPictureInfoAid bGetRGBAll bGetScanLines uiGetScanLines
GetScannerInfoPreFrame GetScannerInfoPreFrameUser GetStripInfoAid
bGetStripInfoAid bHexToWordArray ImportFromFile bInit bInit2 bInit3
InitializeScanner bInitImageStruct bInitPlanarImageStruct bInitWorker
bInitWorkerSaveToClientMemory bInitWorkerSaveToSharedMemory
bInitWorkerSaveToDisk bInitWorkerSub InsertPicture LampManualControl bLampWarmUp
bLampWarmupFromStandby bLeave bLoadClientMatrix LoadColNegClient bLoadClientLut
bLoadImage bLoadImageSub bLoadImageFromBuffer bLoadImageFromFile
bLoadImageFromFilm bLoadPic bLoadPicLarge bLock bLockRawStats bNewStrip bOpen
bPermitWorkerThreadCreationScan bPicInterruptHandler bPicToBootLoaderState
bPlanarDataCopy bPrepareColorCorrection bPrepareToScan bProcessHexFileLine
bProcessMofWithAid bProcessorVersion PutCalibrateEEProm
PutCalibrateInfoColorMatrix PutCalibrateInfoDpi PutCalibrateInfoIdentification
PutCalibrateInfoLight PutFilmGuidePosition PutFilmPressureRollerPosition
PutPictureInfo PutPictureInfo1 bPutPictureInfo1 bPutPictureInfoAid bPutRotation
PutScannerInfo000 PutScannerInfo001 PutScannerInfoPreFrameUser
bPutSourceUserRects bPutStripInfoAid bReadAllParams bReadEEPromToRegistry
bReadEEPromData bReadExternalMofFile bReadFile bReadFixedPatternCorrectionFile
bReadHexFile bReadInternalMof bReadSpline bReadLut bReadMatrix_3x10 bReadRawFile
bResetAllPicErrors bResetErrors ResetFactoryDefaults ResetStatusLeds bRetract
bRotate bRotate90L bRotate90R bRotate180 bRotateAndScalePlanar_8 SaveCancel
uiSaveDiskBuffers SaveToClientMemory SaveToDisk bSaveToFile bSaveToMemory
SaveToSharedMemory bScale ScanCancel ScanPictures hrScanPicturesSub iScanStrips
bSelectHardDriveFS bSelectHardDriveP bSendClientCallBack bSensorReset
bSetCurrentScanType bSetFilmLength bSetReadFilePosition bSetUpSaveToSharedMemory
bShutDownPollPPB bStateMachineLedFilm bStopMotors bTimer bUnlock
bUnlockAndFreeVA_Memory bUpdate bVerifyPic bVerifyPicLarge
bWorkingSetSizeDecreaseMin bWorkingSetSizeDecreaseMax bWorkingSetSizeIncrease
bWriteAllParams bWriteEEPromAdjust bWriteEEPromCalWiz bWriteEEPromAdjustValues
bWriteEEProm bWriteFileChunk bHaveIROn bFileIsNewer bReadEEPromSection
bCalibrateFlush bDrvCcdAcquireAndDxStart bKcdfsCorrections bLampSaver
bDrvGetHardwareStatusCcd bDrvCcdClockSpeed bSetEventStates bCancelReadFile
bSetUpListOfRolls bDrvInitCcd bDrvInitLampTemperatures bLogHardwareStatusLamp
bLampDelayOff bChangeLampTemperature bLampTemperatureStable
bDrvGetPpbDeviceBusy bDrvGetByteArray AdjustMotorSpeed bReadPicEEPromByte
bSetLampStartingDifferential bSetLockedLampTemp CalibrateMofReader
bChangeLampTemperatureAfterDelay bChangeLampTemperatureLampOff
bChangeLampTemperatureScanning CalibrateTestTowerSteppers UNNAMED_358 bSetF135
bFindTmax PutPictureBnWEffect PutPictureSaturation""".split()

_EC_1_30 = """InvalidPtrToClientCallback WorkerThreadExists QueryInterface
CoMarshalInterThreadInterfaceInStream UnableToCreateWorkerThread
WorkerThreadCoInitialize WorkerThreadCoGetInterfaceAndReleaseStream
WorkerThreadClientSignal WorkerThreadStartTimeout ScannerNotInitialized
NoPicturesOrStrips TooManyRolls InvalidIndex InvalidMemberVariable
InvalidParameter NoWorkerThreadForMultipleSaveToMemory NoClientMemoryBuffer
OneFileNameForMultipleSaves StartUpError CBAdviseAlreadyCalled CBAdviseNotCalled
InitializeScannerAlreadyCalled AdjustMotorSpeedIsZero NotSupportedByHW
PreviousError CallEnableFullCalibration FileNameListEmpty LampError
ChangingFrameNumberWithAps NotAllowedWithAps""".split()

_EC_101_236 = """ApsCartridgeUnpacked ApsEjectButtonPressed ApsFilmEndError
ApsFilmJamExtract ApsFilmJamScan ApsFilmJamRetract ApsNoCartridge ApsOverflow
ApsPark ApsParkInit ApsUnprocessedFilm BadFileData BadSimulatorFile
BufferDriveMegabytesRollTooSmall CalibratingWizardBusyOrNotRunning
ClientMemoryBufferInUse CreateDirectoryInvalidForm CS_DoubleUnlock
CS_InvalidUnlock CS_NotUnlockedAtExit DllInitialize EEPromAddress
EEPromCorrupted EEPromLength EEPromMemoryAddress EEPromWarningBlank
EEPromWarningCheckSumBad FileNotFound FilmInGuides FirmwareVerification
FocusCurvatureThreshold FocusOutsideRegionOfInterest FocusPredictorThreshold
FocusQuadRegress HardwareFault ImageNotPlanar ImportedFileColor
InsufficientMemoryForSaveToMemory InsufficientMemoryPassedIn LampWarmUpFailure
MemoryNew MissingDllFunction NoFixedPatternCorrection NoHighResolutionBuffer
NoStripsScanned ParsingError PicVersion PreviousHardwareFaultAps
ProcessedRingTailOverflow RegistryRead ScanLineAcquisition
SelfTestFailedCcdStepper SelfTestFailedFilmDrive SelfTestFailedLensStepper
StepperAlreadyMoving StepperDidNotStop StepperPosition SystemInfo TimeOut
WrongByteCount WIN_CancelIo WIN_CreateDirectory WIN_CreateEvent
WIN_CreateFileMapping WIN_DeviceIoControl WIN_FileClose WIN_FileOpen
WIN_FileRead WIN_FileSetPointer WIN_FileTimeToSystemTime WIN_FileWrite
WIN_FindFirstFile WIN_FreeLibrary WIN_GetDiskFreeSpace WIN_GetDiskFreeSpaceEx
WIN_GetFileSize WIN_GetOverlappedResult WIN_LoadLibrary WIN_MapViewOfFile
WIN_OpenEvent WIN_OpenFileMapping WIN_ResetEvent WIN_SetEndOfFile WIN_SetEvent
WIN_SetFilePointerEx WIN_SetProcessWorkingSetSize WIN_UnmapViewOfFile
WIN_VirtualAlloc WIN_VirtualFree WIN_VirtualLock WIN_VirtualUnlock
WIN_WaitForSingleObject DXPotsWillNotAdjust DXNoFilmFound
DXNoGoodBrightSpotFound DXAdjustingPotsForGoodSignal DXBadSwing NoFilmTimeOut
BistPicmMotorFail BistPicmVinFail BistPicm13VFail BistPicm12VFail
BistPicm6VFail BistPicm5VFail BistPicm3VFail UNNAMED_206 BistPiclTeCoolerFail
BistPiclLightBdTempSensorFail BistPiclMotherBdTempSensorFail
BistPiclCurrentDriversCommFail BistPiclMotherBdFpgaCommFail BistPiclDxEntryFail
BistPiclDxExitFail MMX_SSE_NotSupported CcdAtoDGainLimit
ColorCalibrationFromFile FocusBoardNotFound InsufficientLight PicF_NotFound
PollPPBStopped WIN_GetProcessWorkingSetSize CCD_VoltageError VerifyFailed
PollPpbNotOutOfService TIMESTAMP_RECORD InvalidAdjustValue
ResolutionOrFilmFormatChanged UseScratchRemovalChanged ApplyDragChanged
MotorFault_PowerFail MotorFault_FilmDrive MotorFault_CCD_Stepper
MotorFault_Lens_Stepper MotorFault_FilmGuide MotorFault_FilterWheel
PicM_NotFound""".split()

_EC_1001_1022 = """DRV_CannotFindStartOfScanLine DRV_RingTailOverflow DRV_LostSync
DRV_InvalidPacketType DRV_PacketBusy DRV_FifoOverflow DRV_PacketChecksumErr
DRV_PacketOverFlowErr DRV_PacketCommErr DRV_PacketCmdErr
DRV_PacketHostErrorNoAck DRV_PacketHostErrorFormat DRV_PacketHostErrorCkSum
DRV_PacketHostErrorEndPointFormat DRV_PacketHostErrorEndPointTimeOut
DRV_PacketHostErrorEndPointLength DRV_PacketHostErrorAlgo
DRV_PacketHostErrorBus DRV_PacketHostErrorUndefined DRV_PacketReadWriteMismatch
DRV_InfoBufferString DRV_TransferInProgress""".split()

_EC_2001_2022 = """PI_MEMORY PI_CANT_WRITE_PAKONERRORLOGPI PI_CR_INPUT_PROFILE
PI_INPUT_PROFILE PI_OUTPUT_PROFILE PI_RPD2ROMM_PROFILE PI_NO_MMX_PROCESSOR
PI_MIN_MAX_RANGE_RED PI_MIN_MAX_RANGE_GREEN PI_MIN_MAX_RANGE_BLUE
PI_MIN_MAX_RANGE_BRIGHTNESS PI_MIN_MAX_RANGE_CONTRAST PI_INVALID_ORIENTATION
PI_ERROR_SETTING_LOCKBEAM PI_INVALID_FILE_FORMAT
PI_COMBINE_INPUT_OUTPUT_PROFILE PI_CR_COMBINE_INPUT_OUTPUT_PROFILE
PI_KNOWN_EXCEPTION_RECORDED PI_UNKNOWN_EXCEPTION_RECORDED PI_CR_LUTS6
PI_KCDFS_INIT_FAILED PI_NO_DISK_SPACE""".split()

_EC_3001_3019 = """PFS_FileSystemExists PFS_PartitionSelected
PFS_FileSystemNotEmpty PFS_NullFilePointer PFS_FileAlreadyDeleted
PFS_FilePointerDeleted PFS_ReadPastEOF PFS_NotLastStripInFile
PFS_FileLengthNotSet PFS_FileSystemFull PFS_WriteSizeInvalid PFS_WritePastEOF
PFS_BadDrive PFS_InvalidPointer PFS_WritingToCompletedStrip
PFS_NotEnoughDiskSpace PFS_InvalidFileHandle PFS_ReadBeforeBOF
PFS_RollAlreadyRecycled""".split()

_EC_4001_4009 = """DICE_InProgress DICE_NotInProgress DICE_InvalidThread
DICE_InvalidParameter DICE_QueueFull DICE_InternalErr DICE_CodeErr
DICE_CouldNotLoad DICE_Unknown""".split()

_SINGLE = {100: "AidNoRoll", 1000: "DRV_Unknown", 2000: "PI_UNKNOWN",
           3000: "PFS_PartitionAlreadySelected", 4000: "DICE_MemErr",
           11000: "NoScannerDetected"}


def _from(table, base, i):
    return table[i - base] if 0 <= i - base < len(table) else None


def err(code):
    n = (_SINGLE.get(code)
         or _from(_EC_1_30, 1, code) or _from(_EC_101_236, 101, code)
         or _from(_EC_1001_1022, 1001, code) or _from(_EC_2001_2022, 2001, code)
         or _from(_EC_3001_3019, 3001, code) or _from(_EC_4001_4009, 4001, code))
    return "EC_" + n if n else str(code)


def func(i):
    return "FN_" + FUNCS[i - 1] if 1 <= i <= len(FUNCS) else "fn%d" % i


def cls(i):
    return "CN_" + CLASSES[i - 1] if 1 <= i <= len(CLASSES) else "cls%d" % i


def decode(c, f, e, extra):
    return "%s %s %s (%d) %s" % (cls(c), func(f), err(e), e, extra)


def selftest():
    # the exact line the OEM dialog showed us
    assert decode(37, 27, 25, 0) == \
        "CN_CiScanner FN_bCalibrateFindCorrections EC_PreviousError (25) 0", decode(37,27,25,0)
    assert err(1002) == "EC_DRV_RingTailOverflow"
    assert err(1022) == "EC_DRV_TransferInProgress"
    assert err(135) == "EC_HardwareFault"
    assert err(129) == "EC_FilmInGuides"
    assert err(218) == "EC_InsufficientLight"
    assert err(140) == "EC_LampWarmUpFailure"
    assert err(11000) == "EC_NoScannerDetected"
    assert func(197) == "FN_uiGetCorrections"
    assert func(206) == "FN_uiGetScanLines"
    assert func(31) == "FN_bCalibrateFindLedCurrent"
    assert func(189) == "FN_FuncScanPictures"
    assert cls(2) == "CN_Global"
    assert len(FUNCS) == 362, len(FUNCS)
    assert len(CLASSES) == 46, len(CLASSES)
    print("pknames selftest OK (%d funcs, %d classes)" % (len(FUNCS), len(CLASSES)))


if __name__ == "__main__":
    selftest()
