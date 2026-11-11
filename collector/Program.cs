using System.Text.Json;
using System.Text.Json.Serialization;
using LibreHardwareMonitor.Hardware;

// ============================================================================
// Aura Pixie 资源监视采集侧车
//
// 职责：把 LibreHardwareMonitor 的完整传感器树以 NDJSON（每行一个 JSON）输出，
//       供 Python 侧 monitor_hub.py 消费。本程序不含任何指标口径判断——
//       「哪个传感器对应哪个指标」全部由 Python 侧映射表决定，改口径无需重新构建侧车。
//
// 用法：
//   hwcollector.exe                      单次采样后退出（调试/自检用）
//   hwcollector.exe --stream             持续输出，默认 1000ms 一帧
//   hwcollector.exe --stream --interval 500
//   hwcollector.exe --meta               只输出硬件元信息行后退出
//   hwcollector.exe --list               人类可读传感器清单（标定映射表用）
//
// 输出契约（Python 侧按此解析，字段名不得随意变更）：
//   元信息行 {"meta":true,"pid":..,"elevated":true,"dotnet":"8.0.x",
//             "lhm":"0.9.6","hwIndex":[{"type":"Gpu","name":"..","parent":null,"order":0}]}
//   数据行   {"ts":1730000000123,"seq":7,"sensors":[
//              {"hw":"NVIDIA GeForce RTX 3060","hwType":"Gpu",
//               "parent":null,"sensor":"Temperature","name":"GPU Core","value":53.0}]}
//   注：硬件枚举序号只在 meta 行的 hwIndex 里给出（order 字段），数据行不带 hwOrder。
//
// 权限：CPU 温度 / CPU 功耗 / 硬盘 SMART 温度 需要管理员权限 + PawnIO 驱动。
//       非管理员下这些传感器静默缺失（不是报错），故 elevated 字段必须透传给上层判断。
// ============================================================================

var opts = new JsonSerializerOptions
{
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    PropertyNamingPolicy = JsonNamingPolicy.CamelCase
};

bool stream = args.Contains("--stream");
bool metaOnly = args.Contains("--meta");
bool listOnly = args.Contains("--list");
int interval = 1000;
for (int i = 0; i < args.Length - 1; i++)
    if (args[i] == "--interval" && int.TryParse(args[i + 1], out var v) && v >= 100)
        interval = v;

var computer = new Computer
{
    IsCpuEnabled = true,
    IsGpuEnabled = true,
    IsMemoryEnabled = true,
    IsMotherboardEnabled = true,
    IsControllerEnabled = true,
    IsNetworkEnabled = true,
    IsStorageEnabled = true,
    IsBatteryEnabled = true,
    IsPowerMonitorEnabled = true,
};
computer.Open();

// ---------------------------------------------------------------- 元信息 ---
// hwOrder 是同类硬件（如多块硬盘 / 多张显卡）的稳定序号，Python 侧据此
// 顺序罗列 disk.0 / disk.1 / gpu.i / gpu.d，不做字符串猜测。
var hwIndex = new List<object>();
int order = 0;
foreach (var hw in computer.Hardware) order = IndexHw(hw, null, ref order, hwIndex);

var metaLine = JsonSerializer.Serialize(new
{
    meta = true,
    pid = Environment.ProcessId,
    elevated = Environment.IsPrivilegedProcess,
    dotnet = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
    lhm = "0.9.6",
    hwIndex,
}, opts);

if (metaOnly || listOnly)
{
    Console.WriteLine(metaLine);
    if (listOnly) PrintList(computer);
    computer.Close();
    return;
}

Console.WriteLine(metaLine);
Console.Out.Flush();

// ------------------------------------------------------------------ 主循环 ---
if (!stream)
{
    Console.WriteLine(JsonSerializer.Serialize(Frame(computer, 1), opts));
    Console.Out.Flush();
}
else
{
    int seq = 0;
    while (true)
    {
        seq++;
        Console.WriteLine(JsonSerializer.Serialize(Frame(computer, seq), opts));
        Console.Out.Flush();
        Thread.Sleep(interval);
    }
}

computer.Close();

// ------------------------------------------------------------------ 函数 ---
static int IndexHw(IHardware hw, string parent, ref int order, List<object> bag)
{
    bag.Add(new
    {
        type = hw.HardwareType.ToString(),
        name = hw.Name,
        parent,
        order = order++,
    });
    foreach (var sub in hw.SubHardware)
        IndexHw(sub, hw.Name, ref order, bag);   // ref 直传，序号跨子硬件连续递增
    return order;
}

static object Frame(Computer c, int seq)
{
    var sensors = new List<object>();
    foreach (var hw in c.Hardware) Collect(hw, null, sensors);
    return new { ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), seq, sensors };
}

static void Collect(IHardware hw, string parent, List<object> bag)
{
    hw.Update();
    foreach (var s in hw.Sensors)
        if (s.Value.HasValue)
            bag.Add(new
            {
                hw = hw.Name,
                hwType = hw.HardwareType.ToString(),
                parent,
                sensor = s.SensorType.ToString(),
                name = s.Name,
                value = s.Value,
            });
    foreach (var sub in hw.SubHardware) Collect(sub, hw.Name, bag);
}

static void PrintList(Computer c)
{
    foreach (var hw in c.Hardware) Dump(hw, "");

    static void Dump(IHardware hw, string indent)
    {
        hw.Update();
        Console.WriteLine($"{indent}[{hw.HardwareType}] {hw.Name}");
        foreach (var s in hw.Sensors)
            Console.WriteLine($"{indent}    {s.SensorType,-12} {s.Name,-28} {(s.Value.HasValue ? s.Value.ToString() : "(null)")}");
        foreach (var sub in hw.SubHardware) Dump(sub, indent + "  ");
    }
}
