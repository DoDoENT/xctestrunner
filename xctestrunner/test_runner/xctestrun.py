# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Helper class for xctestrun file generated by prebuilt bundles."""

import logging
import os
import shutil
import tempfile

from xctestrunner.shared import bundle_util
from xctestrunner.shared import ios_constants
from xctestrunner.shared import ios_errors
from xctestrunner.shared import plist_util
from xctestrunner.shared import version_util
from xctestrunner.shared import xcode_info_util
from xctestrunner.test_runner import xcodebuild_test_executor


TESTROOT_RELATIVE_PATH = '__TESTROOT__'
_SIGNAL_TEST_WITHOUT_BUILDING_SUCCEEDED = '** TEST EXECUTE SUCCEEDED **'
_SIGNAL_TEST_WITHOUT_BUILDING_FAILED = '** TEST EXECUTE FAILED **'
_LIB_XCTEST_SWIFT_RELATIVE_PATH = 'Developer/usr/lib/libXCTestSwiftSupport.dylib'


class XctestRun(object):
  """Handles running test by xctestrun."""

  def __init__(self, xctestrun_file_path, test_type=None, aut_bundle_id=None):
    """Initializes the XctestRun object.

    If arg work_dir is provided, the original app under test file and test
    bundle file will be moved to work_dir/TEST_ROOT.

    Args:
      xctestrun_file_path: string, path of the xctest run file.
      test_type: string, test type of the test bundle. See supported test types
          in module xctestrunner.shared.ios_constants.
      aut_bundle_id: string, the bundle id of app under test.

    Raises:
      IllegalArgumentError: when the sdk or test type is not supported.
    """
    self._xctestrun_file_path = xctestrun_file_path
    self._xctestrun_file_plist_obj = plist_util.Plist(xctestrun_file_path)
    # xctestrun file always has only key at root dict.
    self._root_key = list(
        self._xctestrun_file_plist_obj.GetPlistField(None).keys())[0]
    self._test_type = test_type
    self._aut_bundle_id = aut_bundle_id

  def SetTestEnvVars(self, env_vars):
    """Sets the additional environment variables of test's process.

    Args:
     env_vars: dict. Both key and value is string.
    """
    if not env_vars:
      return
    test_env_vars = self.GetXctestrunField('EnvironmentVariables')
    if not test_env_vars:
      test_env_vars = {}
    for key, value in env_vars.items():
      test_env_vars[key] = value
    self.SetXctestrunField('EnvironmentVariables', test_env_vars)

  def SetTestArgs(self, args):
    """Sets the additional arguments of test's process.

    Args:
     args: a list of string. Each item is an argument.
    """
    if not args:
      return
    # The generated xctest is always empty. So set it directly.
    self.SetXctestrunField('CommandLineArguments', args)

  def SetAppUnderTestEnvVars(self, env_vars):
    """Sets the additional environment variables of app under test's process.

    Args:
     env_vars: dict. Both key and value is string.
    """
    if not env_vars:
      return
    if self._test_type == ios_constants.TestType.XCUITEST:
      key = 'UITargetAppEnvironmentVariables'
    else:
      key = 'EnvironmentVariables'
    aut_env_vars = self.GetXctestrunField(key)
    if not aut_env_vars:
      aut_env_vars = {}
    for env_key, env_value in env_vars.items():
      aut_env_vars[env_key] = env_value
    self.SetXctestrunField(key, aut_env_vars)

  def SetAppUnderTestArgs(self, args):
    """Sets the additional arguments of app under test's process.

    Args:
     args: a list of string. Each item is an argument.
    """
    if not args:
      return
    if self._test_type == ios_constants.TestType.XCUITEST:
      key = 'UITargetAppCommandLineArguments'
    else:
      key = 'CommandLineArguments'
    self.SetXctestrunField(key, args)

  def SetTestsToRun(self, tests_to_run):
    """Sets the specific test methods/test classes to run in xctestrun file.

    Args:
      tests_to_run: a list of string. The format of each item is
          Test-Class-Name[/Test-Method-Name]
    """
    if not tests_to_run or tests_to_run == ['all']:
      return
    self.SetXctestrunField('OnlyTestIdentifiers', tests_to_run)

  def SetSkipTests(self, skip_tests):
    """Sets the specific test methods/test classes to skip in xctestrun file.

    Args:
      skip_tests: a list of string. The format of each item is
          Test-Class-Name[/Test-Method-Name]
    """
    if not skip_tests:
      return
    self.SetXctestrunField('SkipTestIdentifiers', skip_tests)

  def Run(self, device_id, sdk, derived_data_dir, startup_timeout_sec,
          destination_timeout_sec=None, os_version=None,
          result_bundle_path=None):
    """Runs the test with generated xctestrun file in the specific device.

    Args:
      device_id: ID of the device.
      sdk: shared.ios_constants.SDK, sdk of the device.
      derived_data_dir: path of derived data directory of this test session.
      startup_timeout_sec: seconds until the xcodebuild command is deemed stuck.
      destination_timeout_sec: Wait for the given seconds while searching for
          the destination device.
      os_version: os version of the device.
      result_bundle_path: path to output a xcresult bundle to

    Returns:
      A value of type runner_exit_codes.EXITCODE.
    """
    # When running tests on iOS 12.1 or earlier simulator under Xcode 11 or
    # later, it is required to add swift5 fallback libraries to environment
    # variable.
    # See https://github.com/bazelbuild/rules_apple/issues/684 for context.
    xcode_version = xcode_info_util.GetXcodeVersionNumber()
    if (xcode_version >= 1100 and
        sdk == ios_constants.SDK.IPHONESIMULATOR and os_version and
        version_util.GetVersionNumber(os_version) < 1220):
      new_env_var = {
          'DYLD_FALLBACK_LIBRARY_PATH':
              xcode_info_util.GetSwift5FallbackLibsDir()
      }
      self.SetTestEnvVars(new_env_var)
    logging.info('Running test-without-building with device %s', device_id)
    command = ['xcodebuild', 'test-without-building',
               '-xctestrun', self._xctestrun_file_path,
               '-destination', 'id=%s' % device_id,
               '-derivedDataPath', derived_data_dir]

    if xcode_version >= 1100 and result_bundle_path:
      shutil.rmtree(result_bundle_path, ignore_errors=True)
      command.extend(['-resultBundlePath', result_bundle_path])

    if xcode_version >= 1410:
      command.extend(['-collect-test-diagnostics=never'])

    if destination_timeout_sec:
      command.extend(['-destination-timeout', str(destination_timeout_sec)])
    exit_code, _ = xcodebuild_test_executor.XcodebuildTestExecutor(
        command,
        succeeded_signal=_SIGNAL_TEST_WITHOUT_BUILDING_SUCCEEDED,
        failed_signal=_SIGNAL_TEST_WITHOUT_BUILDING_FAILED,
        sdk=sdk,
        test_type=self.test_type,
        device_id=device_id,
        app_bundle_id=self._aut_bundle_id,
        startup_timeout_sec=startup_timeout_sec).Execute(
            return_output=False, result_bundle_path=result_bundle_path)
    return exit_code

  @property
  def test_type(self):
    if not self._test_type:
      if self.HasXctestrunField('UITargetAppPath'):
        self._test_type = ios_constants.TestType.XCUITEST
      else:
        self._test_type = ios_constants.TestType.XCTEST
    return self._test_type

  def GetXctestrunField(self, field):
    """Gets the specific field in the xctestrun file.

    Args:
      field: string, the field in xctestrun file to view. A field is a list of
          keys separated by colon. E.g. Key1:Key2

    Returns:
      the object of the xctestrun file's field or None if the field does not
      exist in the plist dict.
    """
    try:
      return self._xctestrun_file_plist_obj.GetPlistField(
          '%s:%s' % (self._root_key, field))
    except ios_errors.PlistError:
      return None

  def HasXctestrunField(self, field):
    """Checks if the specific field is in the xctestrun file.

    Args:
      field: string, the field in xctestrun file to view. A field is a list of
          keys separated by colon. E.g. Key1:Key2

    Returns:
      boolean, if the specific field is in the xctestrun file.
    """
    try:
      self._xctestrun_file_plist_obj.GetPlistField(
          '%s:%s' % (self._root_key, field))
      return True
    except ios_errors.PlistError:
      return False

  def SetXctestrunField(self, field, value):
    """Sets the field with provided value in xctestrun file.

    Args:
      field: string, the field to be added in the xctestrun file. A field is a
          list of keys separated by colon. E.g. Key1:Key2
      value: a object, the value of the field to be added. It can be integer,
          bool, string, array, dict.

    Raises:
      ios_errors.PlistError: the field does not exist in the .plist file's dict.
    """
    self._xctestrun_file_plist_obj.SetPlistField(
        '%s:%s' % (self._root_key, field), value)

  def DeleteXctestrunField(self, field):
    """Deletes the field with provided value in xctestrun file.

    Args:
      field: string, the field to be added in the xctestrun file. A field is a
          list of keys separated by colon. E.g. Key1:Key2

    Raises:
      PlistError: the field does not exist in the .plist file's dict.
    """
    self._xctestrun_file_plist_obj.DeletePlistField(
        '%s:%s' % (self._root_key, field))


class XctestRunFactory(object):
  """The class to generate xctestrunfile by building dummy project."""

  def __init__(self, app_under_test_dir, test_bundle_dir,
               sdk=ios_constants.SDK.IPHONESIMULATOR,
               device_arch=ios_constants.ARCH.X86_64,
               test_type=ios_constants.TestType.XCUITEST,
               signing_options=None, work_dir=None):
    """Initializes the XctestRun object.

    If arg work_dir is provided, the original app under test file and test
    bundle file will be moved to work_dir/TEST_ROOT.

    Args:
      app_under_test_dir: string, path of the application to be tested.
      test_bundle_dir: string, path of the test bundle.
      sdk: string, SDKRoot of the test. See supported SDKs in module
          xctestrunner.shared.ios_constants.
      device_arch: ios_constants.ARCH. The architecture of the target device.
      test_type: string, test type of the test bundle. See supported test types
          in module xctestrunner.shared.ios_constants.
      signing_options: dict, the signing app options. See
          ios_constants.SIGNING_OPTIONS_JSON_HELP for details.
      work_dir: string, work directory which contains run files.

    Raises:
      IllegalArgumentError: when the sdk or test type is not supported.
    """
    self._app_under_test_dir = app_under_test_dir
    self._test_bundle_dir = test_bundle_dir
    self._test_name = os.path.splitext(os.path.basename(test_bundle_dir))[0]
    self._sdk = sdk
    self._device_arch = device_arch
    self._test_type = test_type
    if self._sdk == ios_constants.SDK.IPHONEOS:
      self._on_device = True
      self._signing_options = signing_options
    else:
      self._on_device = False
      if signing_options:
        logging.info(
            'The signing options only works on sdk iphoneos, but current sdk '
            'is %s', self._sdk)
      self._signing_options = {}
    self._work_dir = work_dir
    self._test_root_dir = None
    self._xctestrun_obj = None
    self._xctestrun_dict = None
    self._delete_work_dir = False
    self._ValidateArguments()

  def __enter__(self):
    return self.GenerateXctestrun()

  def __exit__(self, unused_type, unused_value, unused_traceback):
    """Deletes the temp directories."""
    self.Close()

  def GenerateXctestrun(self):
    """Generates a xctestrun object according to arguments.

    The xctestrun file will be generated under work_dir/TEST_ROOT. The app under
    test and test bundle will also be moved under work_dir/TEST_ROOT.

    Returns:
      a xctestrun.XctestRun object.
    """
    if self._xctestrun_obj:
      return self._xctestrun_obj
    if self._work_dir:
      self._test_root_dir = os.path.join(self._work_dir, 'TEST_ROOT')
      xctestrun_file_path = os.path.join(self._test_root_dir, 'test.xctestrun')
      if os.path.exists(xctestrun_file_path):
        logging.info('Skips generating xctestrun file which is generated.')
        self._xctestrun_obj = XctestRun(xctestrun_file_path)
        return self._xctestrun_obj

    logging.info('Generating xctestrun file.')
    if self._work_dir:
      if not os.path.exists(self._work_dir):
        os.mkdir(self._work_dir)
    else:
      self._work_dir = tempfile.mkdtemp()
      self._delete_work_dir = True
    self._test_root_dir = os.path.join(self._work_dir, 'TEST_ROOT')
    if not os.path.exists(self._test_root_dir):
      os.mkdir(self._test_root_dir)

    if  self._test_type != ios_constants.TestType.LOGIC_TEST:
      self._app_under_test_dir = _MoveAndReplaceFile(
          self._app_under_test_dir, self._test_root_dir)

    if self._test_type == ios_constants.TestType.XCUITEST:
      self._GenerateTestRootForXcuitest()
    elif self._test_type == ios_constants.TestType.XCTEST:
      self._GenerateTestRootForXctest()
    elif self._test_type == ios_constants.TestType.LOGIC_TEST:
      self._GenerateTestRootForLogicTest()

    xctestrun_file_path = os.path.join(self._test_root_dir, 'test.xctestrun')
    plist_util.Plist(xctestrun_file_path).SetPlistField('Runner',
                                                        self._xctestrun_dict)

    # Replace the TESTROOT absolute path with __TESTROOT__ in xctestrun file.
    # Then the xctestrun file is not only used in the local machine, but also
    # other mac machines.
    with open(xctestrun_file_path, 'r') as xctestrun_file:
      xctestrun_file_content = xctestrun_file.read()
    xctestrun_file_content = xctestrun_file_content.replace(
        self._test_root_dir, TESTROOT_RELATIVE_PATH)
    with open(xctestrun_file_path, 'w+') as xctestrun_file:
      xctestrun_file.write(xctestrun_file_content)
    self._xctestrun_obj = XctestRun(
        xctestrun_file_path,
        test_type=self._test_type,
        aut_bundle_id=(bundle_util.GetBundleId(self._app_under_test_dir)
                       if self._app_under_test_dir else None))
    return self._xctestrun_obj

  def Close(self):
    """Deletes the temp directories."""
    if self._delete_work_dir and os.path.exists(self._work_dir):
      shutil.rmtree(self._work_dir)

  def _ValidateArguments(self):
    """Checks whether the arguments of this class are valid.

    Raises:
      IllegalArgumentError: when the sdk or test type is not supported.
    """
    if self._sdk not in ios_constants.SUPPORTED_SDKS:
      raise ios_errors.IllegalArgumentError(
          'The sdk %s is not supported. Supported sdks are %s.'
          % (self._sdk, ios_constants.SUPPORTED_SDKS))
    if self._test_type not in ios_constants.SUPPORTED_TEST_TYPES:
      raise ios_errors.IllegalArgumentError(
          'The test type %s is not supported. Supported test types are %s.'
          % (self._test_type, ios_constants.SUPPORTED_TEST_TYPES))
    if (self._test_type == ios_constants.TestType.LOGIC_TEST and
        self._on_device):
      raise ios_errors.IllegalArgumentError(
          'Only support running logic test on sdk iphonesimulator. '
          'Current sdk is %s' % self._sdk)

  def _GenerateTestRootForXcuitest(self):
    """Generates the test root for XCUITest.

    The approach constructs test.xctestrun and uitest runner app from Xcode.
    Then copies app under test, test bundle, test.xctestrun and uitest
    runner app to test root directory.
    """
    platform_path = xcode_info_util.GetSdkPlatformPath(self._sdk)
    platform_library_path = os.path.join(platform_path, 'Developer/Library')
    uitest_runner_app = self._GetUitestRunnerAppFromXcode(platform_library_path)
    self._PrepareUitestInRunerApp(uitest_runner_app)

    if self._on_device:
      runner_app_embedded_provision = os.path.join(
          uitest_runner_app, 'embedded.mobileprovision')
      use_customized_provision = False
      if self._signing_options:
        customized_runner_app_provision = self._signing_options.get(
            'xctrunner_app_provisioning_profile')
        if customized_runner_app_provision:
          shutil.copyfile(customized_runner_app_provision,
                          runner_app_embedded_provision)
          use_customized_provision = True
        if self._signing_options.get('xctrunner_app_enable_ui_file_sharing'):
          try:
            # Don't resign the uitest runner app here since it will be resigned
            # with passing entitlements and identity later.
            bundle_util.EnableUIFileSharing(uitest_runner_app, resigning=False)
          except ios_errors.BundleError as e:
            logging.warning(str(e))

      # If customized runner app provision is not provided, runner app will
      # use app under test's embedded provision as embedded provision.
      if not use_customized_provision:
        app_under_test_embedded_provision = os.path.join(
            self._app_under_test_dir, 'embedded.mobileprovision')
        shutil.copyfile(app_under_test_embedded_provision,
                        runner_app_embedded_provision)

      test_bundle_team_id = bundle_util.GetDevelopmentTeam(
          self._test_bundle_dir)
      full_test_bundle_id = '%s.%s' % (
          test_bundle_team_id, bundle_util.GetBundleId(self._test_bundle_dir))
      entitlements_dict = {
          'application-identifier': full_test_bundle_id,
          'com.apple.developer.team-identifier': test_bundle_team_id,
          'get-task-allow': True,
          'keychain-access-groups': [full_test_bundle_id],
      }
      entitlements_plist_path = os.path.join(uitest_runner_app,
                                             'RunnerEntitlements.plist')
      plist_util.Plist(entitlements_plist_path).SetPlistField(
          None, entitlements_dict)

      test_bundle_signing_identity = bundle_util.GetCodesignIdentity(
          self._test_bundle_dir)

      runner_app_frameworks_dir = os.path.join(uitest_runner_app, 'Frameworks')
      os.mkdir(runner_app_frameworks_dir)
      _CopyAndSignFramework(
          os.path.join(platform_library_path, 'Frameworks/XCTest.framework'),
          runner_app_frameworks_dir, test_bundle_signing_identity)
      _CopyAndSignFramework(
          os.path.join(platform_library_path,
                       'PrivateFrameworks/XCTAutomationSupport.framework'),
          runner_app_frameworks_dir, test_bundle_signing_identity)
      if xcode_info_util.GetXcodeVersionNumber() >= 1100:
        _CopyAndSignLibFile(
            os.path.join(platform_path, _LIB_XCTEST_SWIFT_RELATIVE_PATH),
            runner_app_frameworks_dir, test_bundle_signing_identity)
      bundle_util.CodesignBundle(
          uitest_runner_app,
          entitlements_plist_path=entitlements_plist_path,
          identity=test_bundle_signing_identity)
      if xcode_info_util.GetXcodeVersionNumber() >= 1300:
        _CopyAndSignFramework(
          os.path.join(platform_library_path,
                       'PrivateFrameworks/XCUIAutomation.framework'),
          runner_app_frameworks_dir, test_bundle_signing_identity)
        _CopyAndSignFramework(
          os.path.join(platform_library_path,
                       'PrivateFrameworks/XCTestCore.framework'),
          runner_app_frameworks_dir, test_bundle_signing_identity)
        _CopyAndSignFramework(
          os.path.join(platform_library_path,
                       'PrivateFrameworks/XCUnit.framework'),
          runner_app_frameworks_dir, test_bundle_signing_identity)
      if xcode_info_util.GetXcodeVersionNumber() >= 1430:
         _CopyAndSignFramework(
           os.path.join(platform_library_path,
                    'PrivateFrameworks/XCTestSupport.framework'),
           runner_app_frameworks_dir, test_bundle_signing_identity)
      bundle_util.CodesignBundle(self._test_bundle_dir)
      bundle_util.CodesignBundle(self._app_under_test_dir)

    platform_name = 'iPhoneOS' if self._on_device else 'iPhoneSimulator'
    developer_path = '__PLATFORMS__/%s.platform/Developer' % platform_name
    test_envs = {
        'DYLD_FRAMEWORK_PATH': '__TESTROOT__:{developer}/Library/Frameworks:'
                               '{developer}/Library/PrivateFrameworks'.format(
                                   developer=developer_path),
        'DYLD_LIBRARY_PATH': '__TESTROOT__:%s/usr/lib' % developer_path
    }
    self._xctestrun_dict = {
        'ProductModuleName': self._test_name.replace("-", "_"),
        'IsUITestBundle': True,
        'SystemAttachmentLifetime': 'keepNever',
        'TestBundlePath': self._test_bundle_dir,
        'TestHostPath': uitest_runner_app,
        'UITargetAppPath': self._app_under_test_dir,
        'UserAttachmentLifetime': 'keepNever',
        'TestingEnvironmentVariables': test_envs,
        'DependentProductPaths': [
            self._app_under_test_dir,
            self._test_bundle_dir],
    }

  def _GetUitestRunnerAppFromXcode(self, platform_library_path):
    """Gets the test runner app for uitest from Xcode directory.

    The test runner app will be copied to TEST_ROOT and renamed to
    {test-bundle-name}-Runner.app.

    Args:
      platform_library_path: string, the library path of the sdk platform.
    Returns:
      A string, the path of uitest runner app.
    """
    test_bundle_name = os.path.splitext(
        os.path.basename(self._test_bundle_dir))[0]
    xctrunner_app = os.path.join(
        platform_library_path, 'Xcode/Agents/XCTRunner.app')
    uitest_runner_app_name = '%s-Runner' % test_bundle_name
    uitest_runner_app = os.path.join(self._test_root_dir,
                                     uitest_runner_app_name + '.app')
    if os.path.exists(uitest_runner_app):
      shutil.rmtree(uitest_runner_app)
    shutil.copytree(xctrunner_app, uitest_runner_app)
    uitest_runner_exec = os.path.join(uitest_runner_app, uitest_runner_app_name)
    shutil.move(
        os.path.join(uitest_runner_app, 'XCTRunner'), uitest_runner_exec)
    # XCTRunner is multi-archs. When launching XCTRunner on arm64e device, it
    # will be launched as arm64e process by default. If the test bundle is arm64
    # bundle, the XCTRunner which hosts the test bundle will fail to be
    # launched. So removing the arm64e arch from XCTRunner can resolve this
    # case.
    test_executable = os.path.join(self._test_bundle_dir, test_bundle_name)
    if self._device_arch == ios_constants.ARCH.ARM64E:
      test_archs = bundle_util.GetFileArchTypes(test_executable)
      if ios_constants.ARCH.ARM64E not in test_archs:
        bundle_util.RemoveArchType(uitest_runner_exec,
                                   ios_constants.ARCH.ARM64E)
    # XCTRunner is multi-archs. When launching XCTRunner on Apple silicon
    # simulator, it will be launched as arm64 process by default. If the test
    # bundle is still x86_64, the XCTRunner which hosts the test bundle will
    # fail to be launched. So removing the arm64 arch from XCTRunner can
    # resolve this case.
    elif not self._on_device:
      test_archs = bundle_util.GetFileArchTypes(test_executable)
      if ios_constants.ARCH.X86_64 in test_archs:
        bundle_util.RemoveArchType(uitest_runner_exec, ios_constants.ARCH.ARM64)

    runner_app_info_plist_path = os.path.join(uitest_runner_app, 'Info.plist')
    info_plist = plist_util.Plist(runner_app_info_plist_path)
    info_plist.SetPlistField('CFBundleName', uitest_runner_app_name)
    info_plist.SetPlistField('CFBundleExecutable', uitest_runner_app_name)
    info_plist.SetPlistField('CFBundleIdentifier',
                             'com.apple.test.' + uitest_runner_app_name)

    return uitest_runner_app

  def _PrepareUitestInRunerApp(self, uitest_runner_app):
    """Moves the test bundle to be hosted by UITest runner app."""
    runner_app_plugins_dir = os.path.join(uitest_runner_app, 'PlugIns')
    os.mkdir(runner_app_plugins_dir)
    # The test bundle should not exist under the new runner.app.
    if os.path.islink(self._test_bundle_dir):
      # The test bundle under PlugIns can not be symlink since it will cause
      # app installation error.
      new_test_bundle_path = os.path.join(
          runner_app_plugins_dir, os.path.basename(self._test_bundle_dir))
      shutil.copytree(self._test_bundle_dir, new_test_bundle_path)
      self._test_bundle_dir = new_test_bundle_path
    else:
      self._test_bundle_dir = _MoveAndReplaceFile(self._test_bundle_dir,
                                                  runner_app_plugins_dir)

  def _GenerateTestRootForXctest(self):
    """Generates the test root for XCTest.

    The approach constructs test.xctestrun from Xcode. Then copies app under
    test, test bundle and test.xctestrun to test root directory.
    """
    app_under_test_plugins_dir = os.path.join(
        self._app_under_test_dir, 'PlugIns')
    if not os.path.exists(app_under_test_plugins_dir):
      os.mkdir(app_under_test_plugins_dir)
    new_test_bundle_path = os.path.join(
        app_under_test_plugins_dir, os.path.basename(self._test_bundle_dir))
    # The test bundle under PlugIns can not be symlink since it will cause
    # app installation error.
    if os.path.islink(self._test_bundle_dir):
      shutil.copytree(self._test_bundle_dir, new_test_bundle_path)
      self._test_bundle_dir = new_test_bundle_path
    elif new_test_bundle_path != self._test_bundle_dir:
      self._test_bundle_dir = _MoveAndReplaceFile(
          self._test_bundle_dir, app_under_test_plugins_dir)

    if self._on_device:
      platform_path = xcode_info_util.GetSdkPlatformPath(self._sdk)
      app_under_test_frameworks_dir = os.path.join(self._app_under_test_dir,
                                                   'Frameworks')
      if not os.path.exists(app_under_test_frameworks_dir):
        os.mkdir(app_under_test_frameworks_dir)
      app_under_test_signing_identity = bundle_util.GetCodesignIdentity(
          self._app_under_test_dir)
      _CopyAndSignFramework(
          os.path.join(platform_path,
                       'Developer/Library/Frameworks/XCTest.framework'),
          app_under_test_frameworks_dir, app_under_test_signing_identity)
      bundle_injection_lib = os.path.join(
          platform_path, 'Developer/usr/lib/libXCTestBundleInject.dylib')
      _CopyAndSignLibFile(bundle_injection_lib, app_under_test_frameworks_dir,
                          app_under_test_signing_identity)
      if xcode_info_util.GetXcodeVersionNumber() >= 1100:
        _CopyAndSignFramework(
            os.path.join(
                platform_path, 'Developer/Library/PrivateFrameworks/'
                'XCTAutomationSupport.framework'),
            app_under_test_frameworks_dir, app_under_test_signing_identity)
        _CopyAndSignLibFile(
            os.path.join(platform_path, _LIB_XCTEST_SWIFT_RELATIVE_PATH),
            app_under_test_frameworks_dir, app_under_test_signing_identity)
      if xcode_info_util.GetXcodeVersionNumber() >= 1300:
        if xcode_info_util.GetXcodeVersionNumber() >= 1640:
          _CopyAndSignFramework(
              os.path.join(
                  platform_path, 'Developer/Library/Frameworks/'
                  'XCUIAutomation.framework'),
              app_under_test_frameworks_dir, app_under_test_signing_identity)
        else:
          _CopyAndSignFramework(
              os.path.join(
                  platform_path, 'Developer/Library/PrivateFrameworks/'
                  'XCUIAutomation.framework'),
              app_under_test_frameworks_dir, app_under_test_signing_identity)
        _CopyAndSignFramework(
            os.path.join(
                platform_path, 'Developer/Library/PrivateFrameworks/'
                'XCTestCore.framework'),
            app_under_test_frameworks_dir, app_under_test_signing_identity)
        _CopyAndSignFramework(
            os.path.join(
                platform_path, 'Developer/Library/PrivateFrameworks/'
                'XCUnit.framework'),
            app_under_test_frameworks_dir, app_under_test_signing_identity)
      if xcode_info_util.GetXcodeVersionNumber() >= 1430:
        _CopyAndSignFramework(
            os.path.join(
                platform_path,
                'Developer/Library/PrivateFrameworks/XCTestSupport.framework',
            ),
            app_under_test_frameworks_dir,
            app_under_test_signing_identity,
        )
      bundle_util.CodesignBundle(self._test_bundle_dir)
      bundle_util.CodesignBundle(self._app_under_test_dir)

    app_under_test_name = os.path.splitext(
        os.path.basename(self._app_under_test_dir))[0]
    platform_name = 'iPhoneOS' if self._on_device else 'iPhoneSimulator'
    developer_path = '__PLATFORMS__/%s.platform/Developer' % platform_name

    if self._on_device:
      dyld_insert_libs = '__TESTHOST__/Frameworks/libXCTestBundleInject.dylib'
    else:
      dyld_insert_libs = ('%s/usr/lib/libXCTestBundleInject.dylib' %
                          developer_path)
    test_envs = {
        'XCInjectBundleInto': os.path.join('__TESTHOST__', app_under_test_name),
        'DYLD_FRAMEWORK_PATH': '__TESTROOT__:{developer}/Library/Frameworks:'
                               '{developer}/Library/PrivateFrameworks'.format(
                                   developer=developer_path),
        'DYLD_INSERT_LIBRARIES': dyld_insert_libs,
        'DYLD_LIBRARY_PATH': '__TESTROOT__:%s/usr/lib:' % developer_path
    }
    self._xctestrun_dict = {
        'ProductModuleName': self._test_name.replace("-", "_"),
        'TestHostPath': self._app_under_test_dir,
        'TestBundlePath': self._test_bundle_dir,
        'IsAppHostedTestBundle': True,
        'TestingEnvironmentVariables': test_envs
    }

  def _GenerateTestRootForLogicTest(self):
    """Generates the test root for Logic test.

    The approach constructs test.xctestrun from Xcode. Then copies test bundle
    and test.xctestrun to test root directory.
    """
    dyld_framework_path = os.path.join(
        xcode_info_util.GetSdkPlatformPath(self._sdk),
        'Developer/Library/Frameworks')
    test_envs = {
        'DYLD_FRAMEWORK_PATH': dyld_framework_path,
        'DYLD_LIBRARY_PATH': dyld_framework_path
    }
    self._xctestrun_dict = {
        'ProductModuleName': self._test_name.replace("-", "_"),
        'TestBundlePath': self._test_bundle_dir,
        'TestHostPath': xcode_info_util.GetXctestToolPath(self._sdk),
        'TestingEnvironmentVariables': test_envs,
    }


def _MoveAndReplaceFile(src_file, target_parent_dir):
  """Moves the file under target directory and replace it if it exists."""
  new_file_path = os.path.join(
      target_parent_dir, os.path.basename(src_file))
  if os.path.exists(new_file_path):
    shutil.rmtree(new_file_path)
  shutil.move(src_file, new_file_path)
  return new_file_path


def _CopyAndSignFramework(src_framework, target_parent_dir, signing_identity):
  """Copies the framework to the directory and signs the file with identity."""
  file_name = os.path.basename(src_framework)
  target_path = os.path.join(target_parent_dir, file_name)
  if os.path.exists(target_path):
    shutil.rmtree(target_path)
  shutil.copytree(src_framework, target_path)
  bundle_util.CodesignBundle(target_path, identity=signing_identity)


def _CopyAndSignLibFile(src_lib, target_parent_dir, signing_identity):
  """Copies the library to the directory and signs the file with identity."""
  file_name = os.path.basename(src_lib)
  target_path = os.path.join(target_parent_dir, file_name)
  if os.path.exists(target_path):
    os.remove(target_path)
  shutil.copy(src_lib, target_path)
  bundle_util.CodesignBundle(target_path, identity=signing_identity)
