using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: AssemblyTitle("ATNOS")]
[assembly: AssemblyProduct("ATNOS Local AI Agent")]
[assembly: AssemblyCompany("ATNOS")]
[assembly: AssemblyDescription("ATNOS local AI agent desktop shell")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace AtnosDesktopShell
{
    internal static class Program
    {
        private const string SingleInstanceMutexName = @"Local\AtnosDesktopShellV2";
        private const string ActivationEventName = @"Local\AtnosDesktopShellActivateV2";

        private static Mutex _singleInstanceMutex;
        private static EventWaitHandle _activationEvent;

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern int SetCurrentProcessExplicitAppUserModelID(string appID);

        [STAThread]
        private static void Main(string[] args)
        {
            TrySetAppIdentity();

            bool createdNew;
            _singleInstanceMutex = new Mutex(true, SingleInstanceMutexName, out createdNew);
            if (!createdNew)
            {
                SignalExistingInstance();
                _singleInstanceMutex.Dispose();
                return;
            }

            string appRoot = AppDomain.CurrentDomain.BaseDirectory;
            string defaultUrl = "http://127.0.0.1:3000/";
            string targetUrl = args.Length > 0 && !string.IsNullOrWhiteSpace(args[0]) ? args[0] : defaultUrl;
            string dataRoot = Environment.GetEnvironmentVariable("ATNOS_WEBVIEW2_USER_DATA_DIR");
            if (string.IsNullOrWhiteSpace(dataRoot))
            {
                dataRoot = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "Atnos",
                    "data",
                    "webview2-profile"
                );
            }

            Directory.CreateDirectory(dataRoot);

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            ShellForm shellForm = new ShellForm(appRoot, dataRoot, targetUrl);
            StartActivationListener(shellForm);
            try
            {
                Application.Run(shellForm);
            }
            finally
            {
                if (_activationEvent != null)
                {
                    _activationEvent.Dispose();
                }
                _singleInstanceMutex.ReleaseMutex();
                _singleInstanceMutex.Dispose();
            }
        }

        private static void SignalExistingInstance()
        {
            try
            {
                using (EventWaitHandle activationEvent = EventWaitHandle.OpenExisting(ActivationEventName))
                {
                    activationEvent.Set();
                }
            }
            catch (WaitHandleCannotBeOpenedException)
            {
                // The first process is still initializing. A subsequent shortcut click
                // can activate it after the event has been created.
            }
        }

        private static void StartActivationListener(ShellForm shellForm)
        {
            _activationEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ActivationEventName);
            ThreadPool.QueueUserWorkItem(_ =>
            {
                while (!shellForm.IsDisposed)
                {
                    try
                    {
                        _activationEvent.WaitOne();
                        if (shellForm.IsDisposed)
                        {
                            return;
                        }
                        shellForm.BeginInvoke(new Action(shellForm.RestoreAndActivate));
                    }
                    catch (ObjectDisposedException)
                    {
                        return;
                    }
                    catch (InvalidOperationException)
                    {
                        return;
                    }
                }
            });
        }

        private static void TrySetAppIdentity()
        {
            try
            {
                SetCurrentProcessExplicitAppUserModelID("ATNOS.LocalAgent.DesktopShell");
            }
            catch
            {
            }
        }
    }

    internal sealed class ShellForm : Form
    {
        private const int HotkeyIdAmbientMute = 0xA71F;
        private const int WmHotkey = 0x0312;
        private const uint ModControl = 0x0002;
        private const uint ModShift = 0x0004;
        private const int VkM = 0x4D;

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, int vk);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        private readonly WebView2 _webView;
        private readonly Label _errorLabel;
        private readonly string _appRoot;
        private readonly string _userDataDir;
        private readonly string _targetUrl;
        private readonly NotifyIcon _trayIcon;
        private readonly ContextMenuStrip _trayMenu;
        private bool _ambientHotkeyRegistered;
        private bool _allowExit;
        private bool _trayBalloonShown;

        public ShellForm(string appRoot, string userDataDir, string targetUrl)
        {
            _appRoot = appRoot;
            _userDataDir = userDataDir;
            _targetUrl = targetUrl;

            Text = "ATNOS";
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(1180, 760);
            ClientSize = new Size(1440, 960);
            BackColor = Color.FromArgb(10, 14, 23);
            ShowInTaskbar = true;
            FormBorderStyle = FormBorderStyle.Sizable;

            string iconPath = Path.Combine(appRoot, "atnos-logo.ico");
            if (File.Exists(iconPath))
            {
                Icon = new Icon(iconPath);
            }

            _webView = new WebView2
            {
                Dock = DockStyle.Fill,
                Visible = true,
                DefaultBackgroundColor = Color.FromArgb(10, 14, 23),
            };

            _errorLabel = new Label
            {
                Dock = DockStyle.Fill,
                Visible = true,
                ForeColor = Color.FromArgb(200, 200, 200),
                BackColor = Color.FromArgb(10, 14, 23),
                Font = new Font("Segoe UI", 11f, FontStyle.Regular),
                Padding = new Padding(32),
                TextAlign = ContentAlignment.MiddleCenter,
                Text = "Uruchamiam ATNOS…",
            };

            Controls.Add(_webView);
            Controls.Add(_errorLabel);
            _errorLabel.BringToFront();

            _trayMenu = BuildTrayMenu();
            _trayIcon = new NotifyIcon
            {
                Icon = Icon != null ? Icon : SystemIcons.Application,
                Text = "ATNOS",
                ContextMenuStrip = _trayMenu,
                Visible = true,
            };
            _trayIcon.DoubleClick += (sender, eventArgs) => RestoreFromTray();

            Resize += (sender, eventArgs) =>
            {
                if (WindowState == FormWindowState.Minimized)
                {
                    HideToTray();
                }
            };
            FormClosing += OnShellFormClosing;
            Shown += async (_, __) => await InitializeAsync();
            FormClosed += OnShellFormClosed;
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            _ambientHotkeyRegistered = RegisterHotKey(Handle, HotkeyIdAmbientMute, ModControl | ModShift, VkM);
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WmHotkey && m.WParam.ToInt32() == HotkeyIdAmbientMute)
            {
                TryPostAmbientMuteMessage();
            }

            base.WndProc(ref m);
        }

        private async System.Threading.Tasks.Task InitializeAsync()
        {
            try
            {
                CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, _userDataDir);
                await _webView.EnsureCoreWebView2Async(environment);
                string capabilityBootstrapScript = BuildCapabilityBootstrapScript();
                if (!string.IsNullOrWhiteSpace(capabilityBootstrapScript))
                {
                    await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                        capabilityBootstrapScript
                    );
                }
                _webView.CoreWebView2.Settings.AreDevToolsEnabled = false;
                _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                _webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
                _webView.CoreWebView2.Settings.IsZoomControlEnabled = true;
                _webView.Source = new Uri(_targetUrl);
                _webView.Visible = true;
                _errorLabel.Visible = false;
                _errorLabel.SendToBack();
            }
            catch (Exception ex)
            {
                _webView.Visible = false;
                _errorLabel.Visible = true;
                _errorLabel.BringToFront();
                _errorLabel.TextAlign = ContentAlignment.MiddleLeft;
                _errorLabel.Text =
                    "ATNOS shell nie mogl uruchomic widoku aplikacji.\r\n\r\n"
                    + ex.Message
                    + "\r\n\r\nSprawdz WebView2 runtime i logi launchera.";
            }
        }

        private static string BuildCapabilityBootstrapScript()
        {
            string sessionPath = Environment.GetEnvironmentVariable(
                "ATNOS_LOCAL_CAPABILITY_SESSION_FILE"
            );
            if (string.IsNullOrWhiteSpace(sessionPath) || !File.Exists(sessionPath))
            {
                return null;
            }

            string payload = File.ReadAllText(sessionPath, Encoding.UTF8);
            Match tokenMatch = Regex.Match(
                payload,
                "\\\"protected_token\\\"\\s*:\\s*\\\"(?<value>[0-9A-Fa-f]+)\\\""
            );
            Match expiryMatch = Regex.Match(
                payload,
                "\\\"expires_at\\\"\\s*:\\s*(?<value>[0-9]+)"
            );
            if (!tokenMatch.Success || !expiryMatch.Success)
            {
                return null;
            }

            long expiresAt;
            if (!long.TryParse(expiryMatch.Groups["value"].Value, out expiresAt))
            {
                return null;
            }
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            if (expiresAt <= now)
            {
                return null;
            }

            byte[] protectedBytes = HexToBytes(tokenMatch.Groups["value"].Value);
            byte[] clearBytes = ProtectedData.Unprotect(
                protectedBytes,
                null,
                DataProtectionScope.CurrentUser
            );
            string token = Encoding.Unicode.GetString(clearBytes);
            Array.Clear(clearBytes, 0, clearBytes.Length);
            if (string.IsNullOrWhiteSpace(token))
            {
                return null;
            }

            string escapedToken = token
                .Replace("\\", "\\\\")
                .Replace("'", "\\'")
                .Replace("\r", "")
                .Replace("\n", "");
            return
                "if (location.origin === 'http://127.0.0.1:3000') {"
                + "sessionStorage.setItem('atnos.localCapabilitySession.v1',"
                + "JSON.stringify({token:'"
                + escapedToken
                + "',expiresAt:"
                + expiresAt
                + "}));}";
        }

        private static byte[] HexToBytes(string value)
        {
            if (string.IsNullOrWhiteSpace(value) || value.Length % 2 != 0)
            {
                throw new InvalidDataException("Invalid protected capability payload.");
            }

            byte[] result = new byte[value.Length / 2];
            for (int index = 0; index < result.Length; index++)
            {
                result[index] = Convert.ToByte(value.Substring(index * 2, 2), 16);
            }
            return result;
        }

        private ContextMenuStrip BuildTrayMenu()
        {
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("Pokaz ATNOS", null, (sender, eventArgs) => RestoreFromTray());
            menu.Items.Add("Ukryj do zasobnika", null, (sender, eventArgs) => HideToTray());
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Ustawienia", null, (sender, eventArgs) => NavigateTo("/settings"));
            menu.Items.Add("Odswiez widok", null, (sender, eventArgs) => ReloadView());
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Wylacz ATNOS", null, (sender, eventArgs) => RequestFullShutdown());
            return menu;
        }

        private void HideToTray()
        {
            if (!Visible && WindowState == FormWindowState.Minimized)
            {
                return;
            }

            Hide();
            ShowInTaskbar = false;

            if (!_trayBalloonShown)
            {
                _trayBalloonShown = true;
                _trayIcon.BalloonTipTitle = "ATNOS dziala w tle";
                _trayIcon.BalloonTipText = "Kliknij ikone ATNOS w zasobniku, zeby pokazac lub wylaczyc aplikacje.";
                _trayIcon.ShowBalloonTip(2500);
            }
        }

        public void RestoreAndActivate()
        {
            RestoreFromTray();
            Activate();
            BringToFront();
        }

        private void RestoreFromTray()
        {
            ShowInTaskbar = true;
            Show();
            if (WindowState == FormWindowState.Minimized)
            {
                WindowState = FormWindowState.Normal;
            }

            Activate();
            BringToFront();
        }

        private void NavigateTo(string path)
        {
            try
            {
                Uri target = BuildAppUri(path);
                if (_webView.CoreWebView2 != null)
                {
                    _webView.CoreWebView2.Navigate(target.ToString());
                }
                else
                {
                    _webView.Source = target;
                }
            }
            catch
            {
            }

            RestoreFromTray();
        }

        private Uri BuildAppUri(string path)
        {
            Uri baseUri = new Uri(_targetUrl);
            string origin = baseUri.GetLeftPart(UriPartial.Authority);
            if (!path.StartsWith("/", StringComparison.Ordinal))
            {
                path = "/" + path;
            }

            return new Uri(origin + path);
        }

        private void ReloadView()
        {
            try
            {
                if (_webView.CoreWebView2 != null)
                {
                    _webView.CoreWebView2.Reload();
                }
            }
            catch
            {
            }

            RestoreFromTray();
        }

        private void RequestFullShutdown()
        {
            DialogResult result = MessageBox.Show(
                "Wylaczyc ATNOS i lokalne procesy API/web?",
                "ATNOS",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question
            );

            if (result != DialogResult.Yes)
            {
                return;
            }

            _allowExit = true;
            _trayIcon.Visible = false;
            StartShutdownScript();
            Close();
        }

        private void StartShutdownScript()
        {
            string scriptPath = Path.Combine(_appRoot, "Stop-Atnos.ps1");
            if (!File.Exists(scriptPath))
            {
                return;
            }

            try
            {
                ProcessStartInfo startInfo = new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + scriptPath + "\" -SkipPid " + Process.GetCurrentProcess().Id,
                    WorkingDirectory = _appRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                };
                Process.Start(startInfo);
            }
            catch
            {
            }
        }

        private void OnShellFormClosing(object sender, FormClosingEventArgs eventArgs)
        {
            if (_allowExit || eventArgs.CloseReason == CloseReason.WindowsShutDown || eventArgs.CloseReason == CloseReason.TaskManagerClosing)
            {
                return;
            }

            eventArgs.Cancel = true;
            HideToTray();
        }

        private void OnShellFormClosed(object sender, FormClosedEventArgs eventArgs)
        {
            if (_ambientHotkeyRegistered)
            {
                UnregisterHotKey(Handle, HotkeyIdAmbientMute);
                _ambientHotkeyRegistered = false;
            }

            _trayIcon.Visible = false;
            _trayIcon.Dispose();
            _trayMenu.Dispose();
        }

        private void TryPostAmbientMuteMessage()
        {
            try
            {
                CoreWebView2 coreWebView = _webView.CoreWebView2;
                if (coreWebView != null)
                {
                    coreWebView.PostWebMessageAsJson("{\"type\":\"ambient.toggle_mute\"}");
                }
            }
            catch
            {
            }
        }
    }
}
