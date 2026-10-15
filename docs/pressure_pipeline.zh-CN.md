# 数据检查与离线处理

本模块从huyuxuan触觉诊断中提取通用规则与数组处理方法，不依赖AnySole、torch、训练划分或历史模型快照。第一版不做物理同步、载荷标定或训练窗口认证。

## 使用

“数据检查与处理”页面支持：

1. 保存后自动检查：默认开启，普通录制和纯触觉录制均在文件保存完成后提交。自动任务固定使用original，绝不自动滤波。
2. 手动选择已完成保存的录制目录，选定处理方法，生成新版本。
3. 批量检查历史目录：后台发现meta或左右CSV，包括两脚CSV完全缺失但有meta的录制。
4. 查看每侧/测点检查、时间风险及分段报告；点击时间风险行或曲线定位来源样本。
5. 对照来源和处理后的曲线与48个物理测点。两张热图共用同一侧整个录制的色标。无效NaN/Inf仅为绘图置0，保存值和UNUSABLE标记保留，不能据此当有效压力。
6. 取消任务、打开历史结果或结果文件夹。加载历史结果前在后台核验所有产物哈希，未完成/被改动版本拒绝加载。

“保存后自动检查”开关、独立输出目录、手动处理方法和左右疑似测点会保存到settings.json。旧配置可直接加载。疑似点编号为CSV中的1..48，可留空；默认不假设某只鞋垫的35/38/39有故障。

录制/保存期间，分析在检查点暂停；运行中的numpy/scipy单次调用不能瞬时抢占。只有一个分析worker，防止并行占满CPU。质检失败与原始录制保存失败分开显示。重启后可手动重新提交检查；不自动恢复之前的队列，不把中断run当完成结果。

## 输入与输出

输入是已关闭写入的录制目录：`meta.json`和`pressure_left.csv`/`pressure_right.csv`。CSV必须具有唯一字段`frame_idx,t_us,1..48`；可带valid_mask。meta启用的传感器决定应有哪只脚，合法单脚/相机独立模式不会误判为双脚缺失。meta缺失时保守检查双脚并提示元数据未知。

数据不重新翻转行组。直接按来源CSV的1..48通道编号读取，不额外改变采集器保存的通道顺序。邻点来自打包的foot_sensor_layout，不用一维编号邻接，疑似点不作空间处理供体。

输出必须在原始目录以外。GUI留空时用采集根目录旁的`<save_dir_name>_pressure_analysis`，也可选择独立分析根。Tactile项目建议选择`3_Result/processed/pressure_pipeline`。不要把报告写进原始录制树。

每次运行创建唯一目录，包含：

- run.json：计算状态和数据状态分离。
- input_manifest.json：来源CSV/meta哈希、字节数、期望单脚、布局绑定。
- profile.json：算法、阈值、时钟语义、质量标记位。
- recording_quality.csv、channel_quality.csv、review_queue.csv、foot_pair_review.csv。
- time_events.csv：时间与无效样本的来源行定位；time_segments.csv：处理连续段、实际接收频率、是否跳过。
- raw_check_receipt.json：结果摘要和明确的未评价项。
- processed/<method>/：处理CSV、左右comparison.npz（包含原/处理数组和时间、标记）、processing_receipt.json。
- artifact_manifest.json：整个结果包的文件哈希。

源文件不覆盖、不删除；输出文件原子提交。failed/cancelled版本不能被回放器当成成功版本。结果包自包含用于回放，不依赖原服务器绝对路径。input_manifest中的源文件名用于追溯，不能在没有原始文件时验证其未来是否被修改。

## 状态解释

completed仅表示软件运行完成；data_status另分：

- no_flags_detected：本规则未检出风险，不等于硬件/物理项验收通过。
- review：有幅值、时钟、疑似点或元数据风险，需复核。
- error：应有单脚缺失/空文件/不可读。
- not_applicable：meta说明本录制未启用压力。

压力单位保留来源值；1023是已有模型的处理参考，不是ADC上限；65535不会仅因超过1023被判为无效。D7是高平台/相对其他通道的疑似规则，不自动删除或置零。检查对所有48点执行。

t_us是主机接收代理时间。同一串口读取中的多帧可以共用时间，不自动删除。原始保存器可能已按时间排序，离线报告不声称恢复原到达顺序。没有设备序号时不估计精确丢包数。两脚行数不同也不单独证明丢包。

## 第一版处理范围

| 方法 | 行为 |
|---|---|
| original | 逐元素保留来源值，默认方案 |
| spatial25 | 自身75%＋四个物理邻点均值25% |
| replace4 | 只估计显式配置的疑似点，其他点不改 |
| median3/median5 | 离线居中中值 |
| lowpass6/lowpass10 | 二阶Butterworth双向低通，截止频率6/10Hz |
| hampel5 | 五点居中Hampel，阈值max(3×1.4826×MAD,20) |
| savgol5 | 五点二次SG |
| ema05 | 每段重置EMA，alpha=0.5 |

时间方法只处理数值有效、时间严格递增、无大间断且接收间隔近似均匀的片段。fs取实际间隔中位数；默认允许2%相对间隔偏差或1us绝对偏差。重复/倒退时间会切段，缺口不跨越。短段、过低fs或不规则段保留原值并标PROCESSING_SKIPPED。第一版不擅自构建新时间轴、不重采样，不提供模型96+12特征导出。对原始不规则串口记录，时间滤波可能大部分跳过，这比错误套用固定40Hz更保守。

处理仅对有效行执行。无效行保留原值用于审计并标UNUSABLE。valid_mask表示来源是否原始有效，不因滤波而改写；新增usable列是当前规则的数值资格，不是训练资格。flags保存SOURCE_RETAINED、SPATIALLY_ESTIMATED、TEMPORALLY_FILTERED、UNUSABLE、ABOVE_MODEL_REFERENCE、SUSPECT_CHANNEL、PROCESSING_SKIPPED、DUPLICATE_RECEIVE_TIME；多个位可以并存。

时间双向/居中滤波利用未来样本，只能称离线处理。EMA虽然因果，整套采集/分析系统也未做实时延迟验证。更平滑不等于恢复真实压力，不能声称模型精度必然提升。

## CLI

在采集器项目根目录、原有Python环境执行：

```bash
python -m python_app.pressure_pipeline /path/to/recording_or_batch --output-root /independent/analysis
python -m python_app.pressure_pipeline /path/to/recording --output-root /independent/analysis --method spatial25 --layout-dir foot_sensor_layout --suspect-right 35,38,39
python -m python_app.pressure_pipeline /path/to/recording --output-root /independent/analysis --method lowpass6 --long-gap-ms 300 --model-reference 1023
```

疑似点示例不是所有硬件的默认配置。CLI退出0代表计算完成（可有review/not_applicable），2代表数据error或任务失败；必须检查data_status。

## 验证与部署边界

```bash
python -m unittest python_app.tests.test_pressure_pipeline -v
QT_QPA_PLATFORM=offscreen python -m unittest python_app.tests.test_pressure_pipeline_qt -v
```

第二条是Linux/macOS shell形式；Windows需按其终端语法设置环境变量，或使用本机桌面Qt环境。PyInstaller发布脚本已显式收集scipy及Matplotlib资源，布局CSV沿用原资源打包。

验证包括合成异常、来源哈希、处理边界、后台取消/暂停、配置兼容、两类保存钩子和Qt无硬件回放。不启动相机和串口。Windows发布包构建、连续真实录制和性能对比仍须在目标机器验收；当前Linux验证不能替代该步骤。
