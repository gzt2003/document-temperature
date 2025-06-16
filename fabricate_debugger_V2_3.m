%  
clear
clc
% 更新日志
%{
v2.3
2023/5/31
.加入CW/CCW画整个圆
V2.2
2023/4/26
.加入连续.pgm加工绘set(gca,'DataAspectRatio',[1000 1 1]) 制（速度colorbar仅代表当前程序，上个程序不重绘
.加入画图间隔
.改善figure1、figure2排列（现在figure2依据figure1的位置是紧贴在右侧
.修复0.x速度的绘图颜色
V2.1 
.修复CW绘制颜色
.修复notdwell判断
.修复pie图time题目文本
.修复LINEAR指令未运动报错
V2.0
.重构
V1.6
2022/07/19
.增加函数is_dwell，修复变向dwell判断
V1.5
2022/06/21
.加入以'pgmVariables.mat'命名，在当前文件夹下，缓存上次运行数据
.改善colorbar色阶
V1.4
2022/05/04
.加入变方向是否dwell判定
.修复CW、CCW画线
V1.3
2021/12/1
.改善标识符识别逻辑
.加入是否画图，.pgm中的"'plotSwitch 1"即为开启画图，"'plotSwitch 0"为关闭，默认开启
.加入X、Y、Z轴标识
.改善costY变量以存储最大Y值（之前直接用最后的Y做为使用量
.加入进度条，可以在line 47中更改刷新间隔行数，默认为1000行
.加入速度颜色线，色阶为cool，若颜色变化不明显可在line 48、49中更改最大速度及最小...
速度，默认为100mm/s及0，最大速度设置过低会报错，当前速度低于最小值会画黑线
V1.2
2021/10/20
add:
.增加版本更新功能，基于在matlab的搜索路径中加入函数debuggerVersionCheck()实现
.增加.pgm注释识别，欲在新版本debugger中实现部分绘图功能
.增加.pgm程序语句使用情况计数
change:
.figure()->clf，不新建图窗
remove:
.移除画图首句plot3(0,0,0)，以便于平面观察非3D图
V1.1
2021/10/05
add:
.增加支持标识符G92
.增加运行结束显示使用片长以及运行预估时间
.增加光闸开关线宽
.增加清除无关变量
.增加空行识别
.增加画图首句为plot3(0,0,0)
fixed:
.修正LINEAR计时为0
changed:
.调整标识符判断顺序
.调整CW/CCW中linspace取点数100->10
.调整figure弹出在选文件之后
.调整整个程序只调用两次hold
%}
%% 预设参数
global Velocity_min Velocity_max dtime  %#ok
rowPeriod = 10000;   % 进度条间隔，越大运行越快；减小可以观察画线
Velocity_max = [];	% 若开光闸运行速度在最大与最小之外，线为黑色
Velocity_min = [];	% 设为[]则自动遍历开光闸速度
dtime = 0;  % 画图间隔
continue_fabricata = 1; % 设为1则启动连续加工模式,绘图colorbar仅依据当前程序

%% 保存提供加工程序的工作区
if ~continue_fabricata
    save('temp.mat',"Velocity_min","Velocity_max","dtime","rowPeriod","continue_fabricata")
    clearvars Velocity_min Velocity_max dtime rowPeriod continue_fabricata
    save('pgmVariables.mat')
    clear
    load("temp.mat")
    delete("temp.mat")
end
%% 选取.pgm文件
dbclear if error
[fileName,filePath]=uigetfile('*.pgm','Save File','');

if ~fileName,error('未选取文件');end
a = strfind(fileName,'.');
if ~a,error('选取文件无“.”');end
if fileName(max(a):end) ~= '.pgm',error('未选取.pgm文件');end%#ok
dbstop if error
%% 计算文本总行数
tic,f=fopen([filePath,fileName],'r');
rowTotal = 0;
while ~feof(f)
    rowTotal = rowTotal + sum(fread(f,10000,'char')==10);
end
fclose(f);toc
fprintf('.pgm总行数%d\n',rowTotal);

%% 找出速度最大值、最小值
vmax=0;vmin=0;
if isempty(Velocity_max),vmax=1;Velocity_max=-inf;end
if isempty(Velocity_min),vmin=1;Velocity_min = inf;end
if vmax||vmin
    tic,f=fopen([filePath,fileName],'r');
    while ~feof(f)
        currentLine = fgetl(f);
        s = textscan(currentLine,'%s ');
        switch s{1}{1}
            case 'PSOCONTROL'
                switch s{1}{3} 
                    case 'ON',t=1;
                    case 'OFF',t=0;
                    case 'RESET',t=-1;
                end
            case {'LINEAR','CW','CCW'}
                if t==1
                    Velocity_temp = textscan(currentLine,'%*[^F] F%f');
                    Velocity_temp = Velocity_temp{:};
                    if vmax,Velocity_max = max(Velocity_temp,Velocity_max);end
                    if vmin,Velocity_min = min(Velocity_temp,Velocity_min);end
                end
            case "'plotSwitch"
                switch s{1}{2}
                    case '1',t=1;
                    case '0',t=0;
                end
        end
    end,toc
    fprintf('最大速度：%f\n最小速度：%f\n',Velocity_max,Velocity_min);
end

%% main
f=fopen([filePath,fileName],'r');
Category = {'LINEAR';'CW&CCW';'DWELL'};Time = zeros(length(Category),1);
time = table(Category,Time);time.Properties.VariableUnits = {'' 's'};
Category = {'LINEAR';'DWELL';'PSOCONTROL';'CW&CCW';'INCREMENTAL&ABSOLUTE';'Other'};
Count = zeros(length(Category),1);count = table(Category,Count);
rowNow = 0; % 当前行数
f1=figure(1);hold on;
if ~exist("PointNow","var")
    clf;hold on;
    xlabel('X','Color','r');ylabel('Y','Color','r');zlabel('Z','Color','r');
    view([-1,-5,-3]);
    global PointBefor PointNow PointG92 isABSOLUTE costY...	% 位置状态
        VelocityBefor VelocityNow Vindex notdwell ...    % 运动状态
        plotSwitch pgmF lineColorF lineWidth lineColor  %#ok    % 绘图参数
    PointBefor=[0,0,0];PointNow=[0,0,0];PointG92=[0,0,0];costY=0;
    VelocityBefor=[0,0,0];VelocityNow=[0,0,0];Vindex = 1;
    plotSwitch=1;pgmF=nan;lineWidth=0.1;lineColor=[1 1 1];
end
if Velocity_min<1,Vindex = 1/Velocity_min;end
Velocity_min=Vindex*Velocity_min-1;Velocity_max=Vindex*Velocity_max;
lineColorF = colormap(cool(fix(Velocity_max - Velocity_min)+1));
notdwell=[0,0,0];
waitBar=waitbar(0,'1','name','SIMULATING...');
while ~feof(f)
    rowNow = rowNow + 1;
    if mod(rowNow,rowPeriod) == 0
        waitbar(rowNow/rowTotal,waitBar,sprintf('%.3g%%，rowNow=%d\nrowTotal=%d',...
            rowNow/rowTotal*100,rowNow,rowTotal));
    end
    currentLine = fgetl(f);
    if isempty(currentLine),continue;end % 空行
    s = textscan(currentLine,'%s ');
    if isempty(s),continue;end % 空行
    switch s{1}{1}
        case 'LINEAR'
            time.Time(1)=time.Time(1)+LINEAR(s{1}(2:end)');count.Count(1)=count.Count(1)+1;
        case 'DWELL',time.Time(3)=time.Time(3)+str2double(s{1}(2));count.Count(2)=count.Count(2)+1;
            notdwell=[0,0,0];
        case 'PSOCONTROL',count.Count(3)=count.Count(3)+1;
            switch s{1}{3}
                case 'ON',lineColor = [0 0 0];lineWidth = 1.5;
                case 'OFF',lineColor = [1 1 0];lineWidth = 0.1;
                case 'RESET'
                otherwise,error([mfilename,'：操作符非法'],'PSOCONTROL:非法操作符<%s>',s{1}{3});
            end
        case 'CW',time.Time(2)=time.Time(2)+CW(0,s{1}(2:end)');count.Count(4)=count.Count(4)+1;
        case 'CCW',time.Time(2)=time.Time(2)+CW(1,s{1}(2:end)');count.Count(4)=count.Count(4)+1;
        case 'INCREMENTAL',isABSOLUTE=0;count.Count(5)=count.Count(5)+1;
        case 'ABSOLUTE',isABSOLUTE=1;count.Count(5)=count.Count(5)+1;
        case 'G92',G92(s{1}{2:end});count.Count(6)=count.Count(6)+1;
        case {'G359','ENABLE','METRIC','SECONDS','VELOCITY','PSOOUTPUT'},count.Count(6)=count.Count(6)+1;
        otherwise
            if currentLine(1) == "'"    % 注释行
                if strcmp(s{1}{1},"'plotSwitch")   %绘制判断
                    switch s{1}{2}
                        case '1',plotSwitch=1;
                        case '0',plotSwitch=0;
                        otherwise,error("'plotSwitch非识别操作符：%s",s{1}{2});
                    end
                end
                continue;
            end
            error([mfilename,'：标识符非法'],[currentLine,'\n非法标识符<%s>，line %d'],s{1}{1},rowNow);
    end
end
%% close
fclose(f);delete(waitBar);%hold off;
% colorbar
numV = Velocity_max - Velocity_min;
Velocity_min = Velocity_min + 1;
if numV > 10,c = colorbar('Ticks',0:0.1:1,'TickLabels',{linspace(Velocity_min,Velocity_max,11)/Vindex});
else,c = colorbar('Ticks',linspace(0,1,numV),'TickLabels',{linspace(Velocity_min,Velocity_max,numV)/Vindex});
end,c.Label.String = '开光闸的运动速度';

%视角 比例
% ylim([-0.03,0.03]-0.8)
% set(gca,'DataAspectRatio',[3 1 1]) 
% view(2)



% msgbox
% msg = {'THE SIMULATION HAS BEEN COMPLETED';['片长(Y)总消耗：',num2str(costY),'mm']};
% time_all = sum(time.Time);
% hour = floor(time_all / 3600);minute = floor((time_all - hour*3600) / 60);second = floor(time_all - hour*3600 - minute*60);
% msgTime = '预计加工时间：';
% if hour,msgTime = [msgTime,num2str(hour),'h'];end
% if minute,msgTime = [msgTime,num2str(minute),'m'];end
% if second,msgTime = [msgTime,num2str(second),'s'];end
% msg(3,1) = {msgTime};
% plot summary pie
% f2 = figure(2);f2.Position = [f1.Position(1)+f1.Position(3),f1.Position(2:4)];
% subplot(1,2,1);pie(count.Count,count.Category);title('Count','Color','red');
% subplot(1,2,2);pie(time.Time,time.Category);title('Time','Color','red');
% disp(time),disp(count),disp(msg)
% fabricate_debugger_costY = msg{2};fabricate_debugger_costTime = msg{3};
% if ~continue_fabricata
%     clearvars -except fabricate_debugger_costY fabricate_debugger_costTime time count;
%     load('pgmVariables.mat');delete('pgmVariables.mat');
% end

%% function
function SWITCH(s)
global PointNow PointG92 isABSOLUTE pgmF pgmI pgmJ pgmR Vindex  %#ok
temp = str2double(s(2:end));
switch s(1)
    case 'X'
        if isABSOLUTE,PointNow(1)=temp+PointG92(1);
        else,PointNow(1)=temp+PointNow(1);
        end
    case 'Y'
        if isABSOLUTE,PointNow(2)=temp+PointG92(2);
        else,PointNow(2)=temp+PointNow(2);
        end
    case 'Z'
        if isABSOLUTE,PointNow(3)=temp+PointG92(3);
        else,PointNow(3)=temp+PointNow(3);
        end
    case 'F',pgmF=temp*Vindex;
    case 'I',pgmI=temp;
    case 'J',pgmJ=temp;
    case 'R',pgmR=temp;
    otherwise,error('SWITCH:未能识别%f',s(1));
end
end
function G92(varargin)
global PointG92 PointNow    %#ok
for temp = varargin
    switch temp{1}(1)
        case 'X',PointG92(1)=PointNow(1) - str2double(temp{1}(2:end));
        case 'Y',PointG92(2)=PointNow(2) - str2double(temp{1}(2:end));
        case 'Z',PointG92(3)=PointNow(3) - str2double(temp{1}(2:end));
        otherwise,error([mfilename,'：操作符非法'],'G92:非法操作符<%f>',s(1));
    end
end
end

function time = LINEAR(s)
global PointBefor PointNow costY...	% 位置状态
    VelocityBefor VelocityNow Velocity_min Velocity_max Vindex notdwell ...	% 运动状态
    plotSwitch pgmF lineColorF lineWidth lineColor dtime    %#ok    % 绘图参数
PointBefor = PointNow;VelocityBefor = VelocityNow;  % 保存前一状态
for temp=s,SWITCH(temp{:});end
if PointNow(2)<costY;costY=PointNow(2);end
VelocityNow=PointNow-PointBefor;
if ~any(VelocityNow);time=0;return;end  % 若不动，直接返回
L=norm(VelocityNow);    % 移动距离
VelocityNow=VelocityNow/norm(VelocityNow);
% 判断前一状态与现在状态是否符合物理逻辑关系
% notdwell = is_dwell(VelocityBefor,VelocityNow,notdwell);
% 画图
if plotSwitch
    if dtime,pause(dtime);end
    temp = plot3([PointBefor(1),PointNow(1)],[PointBefor(2),PointNow(2)],...
        [PointBefor(3),PointNow(3)],'.-','LineWidth',lineWidth);
    if ~any(lineColor)&&pgmF>Velocity_min&&pgmF<=Velocity_max
        temp.Color = lineColorF(ceil(pgmF - Velocity_min),:);
    else,temp.Color = lineColor;
    end
end
time = L/pgmF*Vindex;
end
function time = CW(isCCW,s)
global PointBefor PointNow pgmI pgmJ pgmR costY...	% 位置状态
    VelocityBefor VelocityNow Velocity_min Velocity_max Vindex notdwell ...	% 运动状态
    plotSwitch pgmF lineColorF lineWidth lineColor dtime    %#ok    % 绘图参数
curveNum = 9;  % curveNum-1段直线描述一段弧
PointBefor = PointNow;VelocityBefor = VelocityNow;  % 保存前一状态
for temp = s,SWITCH(temp{:});end
PB2PE = PointNow - PointBefor;
Vertical = cross(VelocityBefor,PB2PE);
Vertical = Vertical/norm(Vertical);
% if isCCW;Vertical=-Vertical;end
if isempty(pgmR)
    % 输入为IJ，I对应指定终点第一个轴起始坐标的相对偏移量
    O = PointBefor;
    notdwell = zeros(1,3);
    switch s{1}(1)
        case 'X',O(1) = O(1)+pgmI;notdwell(1)=1;
        case 'Y',O(2) = O(2)+pgmI;notdwell(2)=1;
        case 'Z',O(3) = O(3)+pgmI;notdwell(3)=1;
    end
    switch s{2}(1)
        case 'X',O(1) = O(1)+pgmJ;notdwell(1)=1;
        case 'Y',O(2) = O(2)+pgmJ;notdwell(2)=1;
        case 'Z',O(3) = O(3)+pgmJ;notdwell(3)=1;
    end
    % 半径为起始位置与指定偏移量之间的距离
    pgmR = norm(O - PointBefor);
else    % 输入为R
    notdwell = zeros(1,3);
    % 运动轴置为未dwell
    switch s{1}(1)
        case 'X',notdwell(1)=1;
        case 'Y',notdwell(2)=1;
        case 'Z',notdwell(3)=1;
    end
    switch s{2}(1)
        case 'X',notdwell(1)=1;
        case 'Y',notdwell(2)=1;
        case 'Z',notdwell(3)=1;
    end
    Fr1 = cross(VelocityBefor,Vertical);	% 起点-R*向量Fr1为圆心
    O=PointBefor-pgmR*Fr1;
end
if PB2PE == 0
theta = 2*pi;
pgmR = sqrt(pgmI^2+pgmJ^2);
else
n1=(PointBefor-O)/pgmR;n2=cross(Vertical,n1); %参数方程径向向量
O2PE=PointNow - O;
theta=atan2(dot(O2PE,n2),dot(O2PE,n1));
% if theta<0,theta=theta+2*pi;end
% if isCCW,theta=2*pi-theta;end
T = [n1',n2',O']; % 坐标系变换矩阵
VelocityNow = (T*[-sin(theta);cos(theta);0])';
end
% 画图
if plotSwitch
    if dtime,pause(dtime);end
    if isCCW,t = linspace(theta,0,curveNum);else,t = linspace(0,theta,curveNum);end
    if PB2PE == 0
        Axis = 'XYZ';Axis(strfind(Axis,s{1}(1)))=[];Axis(strfind(Axis,s{2}(1)))=[];
        switch(Axis)
            case 'X',Move.X = ones(1,curveNum)*O(1);
            case 'Y',Move.Y = ones(1,curveNum)*O(2);
            case 'Z',Move.Z = ones(1,curveNum)*O(3);
            otherwise,error('error');
        end
        switch(s{1}(1))
            case 'X',Move.(s{1}(1))=pgmR*cos(t)+O(1);
            case 'Y',Move.(s{1}(1))=pgmR*cos(t)+O(2);
            case 'Z',Move.(s{1}(1))=pgmR*cos(t)+O(3);
        end
        switch(s{2}(1))
            case 'X',Move.(s{2}(1))=pgmR*sin(t)+O(1);
            case 'Y',Move.(s{2}(1))=pgmR*sin(t)+O(2);
            case 'Z',Move.(s{2}(1))=pgmR*sin(t)+O(3);
        end
    else
        A = [pgmR*cos(t);pgmR*sin(t);ones(1,curveNum)];	% 圆平面坐标系的参数方程[x,y,1]
        B = T*A;    % 局部坐标系{X'OY'}转换到{XYZ}
        Move.X = B(1,:);Move.Y = B(2,:);Move.Z = B(3,:);
    end
    if any(Move.Y<costY);costY=min(Move.Y(Move.Y<costY));end   % 判断Y消耗长度
    temp = plot3(Move.X,Move.Y,Move.Z,'-','LineWidth',lineWidth,'Color',lineColor);
    temp2 = plot3([Move.X(1) Move.X(end)],[Move.Y(1) Move.Y(end)],[Move.Z(1) Move.Z(end)],'.'); % 首尾两点标记
    if ~any(lineColor)&&pgmF>Velocity_min&&pgmF<=Velocity_max
        temp.Color = lineColorF(fix(pgmF - Velocity_min),:);
    else,temp.Color = lineColor;
    end,temp2.Color = temp.Color;
end
time = abs(theta)*pi/pgmF*Vindex;
pgmR=[];pgmI=[];pgmJ=[];
end

% function notdwell = is_dwell(v0,v1,notdwell)
% %{
% 判断3轴大幅度变相前是否dwell
% 
% 输入参输：
% v0：前一状态末速度
% v1：现状态初速度
% notdwell：三轴dwell情况，1--未dewell
% 输出结果：
% 若大幅变相未dwell，报错轴、行数、行内容
% 无误则更新notdwell
% %}
% ismove = ones(1,3);
% ismove(v1==0) = v1(v1==0);  % 认为现状态初速度为0的轴为未运动轴\
% if any(notdwell&ismove)	% 运动轴未dwell
%     if dot(v0,v1) < 0.99 % 夹角大于8.11°
%         % 可以报错了
%         global currentLine rowNow %#ok
%         temp = {'x','y','z'};temp = temp(notdwell==1);temp = [temp{:}];
%         error(['变向',temp,'未DWELL：%s，line %d，请尝试',...
%             '调大程序的判断条件0.1，或直接备注掉is_dwell函数'],currentLine,rowNow)
%     end
% end
% notdwell = ismove;
% end