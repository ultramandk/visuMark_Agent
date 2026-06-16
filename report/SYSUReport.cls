\ProvidesClass{SYSUReport}
\LoadClass[12pt,hyperref,a4paper,UTF8]{ctexart}
%%页边距
\RequirePackage[left=2.50cm, right=2.50cm, top=2.50cm, bottom=2.50cm]{geometry}


%%一些必要的宏包
\RequirePackage[unicode=true,colorlinks,urlcolor=blue,linkcolor=blue,bookmarksnumbered=blue]{hyperref}

\RequirePackage{latexsym,amssymb,amsmath,amsbsy,amsopn,amstext,amsthm,amsxtra,color,bm,calc,ifpdf}
\RequirePackage{graphicx}
\RequirePackage{enumerate}
\RequirePackage{fancyhdr}
\RequirePackage{listings}
\RequirePackage{multirow}
\RequirePackage{makeidx}
\RequirePackage{xcolor}
\RequirePackage{fontspec}
\RequirePackage{subfigure}
\PassOptionsToPackage{hyphens}{url}
\RequirePackage{hyperref}
\RequirePackage{booktabs}
\RequirePackage{tcolorbox}

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
%% 字体与标题格式配置
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

%% ---------- 字体设置 ----------
% 英文主字体：Times New Roman
\setmainfont{Times New Roman}
% 中文主字体：宋体 (SimSun)
\setCJKmainfont{SimSun}[BoldFont=SimHei,ItalicFont=KaiTi]
% 中文无衬线字体：黑体 (SimHei) — 用于 subsection
\setCJKsansfont{SimHei}
% 中文等宽字体：仿宋 (FangSong)
\setCJKmonofont{FangSong}
% 定义伪粗宋体，用于 section 标题（宋体本身无粗体字重，用 AutoFakeBold 模拟）
\setCJKfamilyfont{zhsongbold}{SimSun}[AutoFakeBold=2.5]
\newcommand{\songbold}{\CJKfamily{zhsongbold}}

%% ---------- 正文字号：小四 (12bp) ----------
% ctexart 已通过 [12pt] 选项设为基础字号 12bp（即小四）
% 行距 1.5 倍（18bp）
\renewcommand{\normalsize}{\fontsize{12}{18}\selectfont}

%% ---------- 章节编号格式 ----------
% section 用中文数字：一、二、三
\renewcommand{\thesection}{\chinese{section}}
% subsection 用阿拉伯数字：1, 2, 3（不带 section 前缀）
\renewcommand{\thesubsection}{\arabic{subsection}}

%% ---------- 章节标题格式 ----------
\ctexset{
    section = {
        % 小三号 (15bp) 宋体加粗，靠左
        format = \raggedright\songbold\zihao{-3},
        nameformat = {},
        numberformat = {},
        aftername = \quad,
        titleformat = {},
    },
    subsection = {
        % 四号 (14bp) 黑体加粗，靠左
        format = \raggedright\bfseries\heiti\zihao{4},
        nameformat = {},
        numberformat = {},
        % subsection 编号后加 "." 再接标题，如 "1. 标题"
        aftername = .\quad,
        titleformat = {},
    },
}

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

\definecolor{myteal}{cmyk}{1,0,1,0.60} % 主题色改为中大绿
%% 更改摘要二字的样式
\renewcommand{\abstractname}{\textbf{\large {摘\quad 要}}}

%可固定下划线长度
\makeatletter
\newcommand\dlmu[2][4cm]{\hskip1pt\underline{\hb@xt@ #1{\hss#2\hss}}\hskip3pt}
\makeatother

% %更改主题
\hypersetup{
    colorlinks=true,
    linkcolor=black,
    filecolor=blue,
    urlcolor=magenta,
    citecolor=orange,
    linktoc=all,
}

%%对一些autoref的中文引用名作修改
\def\equationautorefname{式}
\def\footnoteautorefname{脚注}
\def\itemautorefname{项}
\def\figureautorefname{图}
\def\tableautorefname{表}
\def\partautorefname{篇}
\def\appendixautorefname{附录}
\def\chapterautorefname{章}
\def\sectionautorefname{节}
\def\subsectionautorefname{小小节}
\def\subsubsectionautorefname{subsubsection}
\def\paragraphautorefname{段落}
\def\subparagraphautorefname{子段落}
\def\FancyVerbLineautorefname{行}
\def\theoremautorefname{定理}

\newtheorem{Theorem}{定理}[section]
\newtheorem{Lemma}[Theorem]{引理}
\newtheorem{Corollary}[Theorem]{推论}
\newtheorem{Proposition}[Theorem]{命题}
\newtheorem{Definition}[Theorem]{定义}
\newtheorem{Example}[Theorem]{例}

%%文本框设置
\newcommand{\tbox}[1]{
    \begin{center}
        \begin{tcolorbox}[colback=gray!10,%gray background
                colframe=black,% black frame colour
                width=8cm,% Use 8cm total width,
                arc=1mm, auto outer arc,
                boxrule=0.5pt,
            ]
            {#1}
        \end{tcolorbox}
    \end{center}
}

\newcommand\lessonTitle[1]{\def\lessonTitle{#1}}
\newcommand\reportTitle[1]{\def\reportTitle{#1}}
\newcommand\stuname[1]{\def\stuname{#1}}
\newcommand\stuid[1]{\def\stuid{#1}}
\newcommand\inst[1]{\def\inst{#1}}
\newcommand\major[1]{\def\major{#1}}
\renewcommand\date[1]{\def\date{#1}}

%%页眉设置
%%页眉设置
\pagestyle{fancy}
\newcommand\headr[1]{\def\headr{#1}\lhead{#1}} % 定义 \headr 命令并将其设置为左页眉
\chead{}
\rhead{\thepage} % 右页眉显示页码
%% 去掉页眉横线
% \renewcommand{\headrulewidth}{0pt}

%% 常用图片文件夹路径
\graphicspath{{figures/}{figure/}{pictures/}{picture/}{pic/}{pics/}{image/}{images/}}

%%在这里可更改封面logo
\newcommand{\cover}{
    %%封面校名logo，图片都在figures文件夹里
    \begin{center}
        \begin{figure}
            \centering
            \vspace{2cm}
            \includegraphics[width=0.6\textwidth]{sysu.jpg}
        \end{figure}

        \vspace*{\fill}

        \heiti \Huge \textbf{{\lessonTitle}} \par
        \vspace{1cm}
        \heiti \Large {\underline{\reportTitle}} \par
        \vspace{3cm}

        \vspace{0.5cm}
        \kaishu\Large 姓\ 名\ \dlmu[9cm]{\stuname} \qquad \\ %姓名 
        \vspace{0.5cm}
        \kaishu\Large 学\ 号\ \dlmu[9cm]{\stuid} \qquad  \\ %学号
        \vspace{0.5cm}
        \kaishu\Large 学\ 院\ \dlmu[9cm]{\inst} \qquad \\ %院所
        \vspace{0.5cm}
        \kaishu\Large 专\ 业\ \dlmu[9cm]{\major} %专业


        \vspace*{\fill}

        \large \date
    \end{center}
}

