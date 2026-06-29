using System.Collections;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using UnityEngine;
using UnityEngine.UI;
using System.IO.Ports;
using System;
using UnityEngine.SceneManagement;
using System.Linq;
using CodeMonkey.Utils;



public class CamTimer : MonoBehaviour
{
    private String commport;
    private int Baudrate;



    SerialPort mySPort;

    long CurrentTime = 0;
    int Animhandler = 0;
    int AnimID = 0;

    private int NumofPlayerPrevious = 1;
    private Boolean AnimPlayOn = false;
    private string IncomeMsg = "";
    public int[] Scorearray = new int[5];
    public int[] WinScorearray = new int[5];
    private int Score = 0;
    private int Numofplayers = 1;
    private int Player = 1;
    private int Balln = 1;
    private int Bonus = 0;
    private int BonusMultiplier = 0;
    private int BonusMultiplierNum = 0;
    private int SumTotal = 0;
    private int Total = 0;

    public Text BallNumText;
    public Text PlayerNumText;
    public Text ScoreText;
    public Text Player1cstext;
    public Text Player2cstext;
    public Text Player3cstext;
    public Text Player4cstext;
    public Text Player1csWin;
    public Text Player2csWin;
    public Text Player3csWin;
    public Text Player4csWin;

    public GameObject Player2;
    public GameObject Player3;
    public GameObject Player4;
    public GameObject Final;


    // Score finalizing Screen

    public Text EndGPlayer;
    public Text EndGScore;
    public Text EndGBonus;
    public Text EndGBonusM;
    public Text Totalscore;





    public GameObject Cam;
    public GameObject StartScr;
    public GameObject ThxScr;
    public GameObject IntroScr;
    private int gameState = 1; // 1 if Intro on, 2 if Game on, 3 if Nextball, 4 if Highscore

    long delej = 0;
    long State3delay = 0;
    long letterdelay = 0;
    public GameObject Multiball1;
    public GameObject Multiball2;
    public GameObject Multiball3;
    public GameObject Multiball4;
    public GameObject Point1;
    public GameObject Point2;
    public GameObject Point3;
    public GameObject Point4;
    public GameObject Point5;
    public GameObject Point6;
    public GameObject Point7;
    public GameObject Point8;
    public GameObject weed;
    public GameObject drift;
    public GameObject jackpot1;
    public GameObject jackpot2;
    public GameObject jackpot3;
    public GameObject jackpot4;
    public GameObject jackpot5;
    public GameObject jackpot6;
    public GameObject bonus1;
    public GameObject bonus2;
    public GameObject bonus3;
    public GameObject bonus4;
    public GameObject extraB;
    public GameObject ufo1;
    public GameObject ufo2;
    public GameObject ufo3;
    public GameObject ufo4;
    public GameObject ufo5;
    public GameObject ufo6;
    public GameObject ufo7;
    public GameObject ufo8;
    public GameObject ufo9;
    public GameObject ufo10;
    public GameObject ufo11;
    public GameObject ufo12;
    public GameObject ufo13;
    public GameObject beer1;
    public GameObject beer2;
    public GameObject beer3;
    public GameObject combo1;
    public GameObject combo2;
    public GameObject combo3;
    public GameObject combo4;
    public GameObject combo5;
    public GameObject combo6;
    public GameObject danger;
    public GameObject tilt;
    public GameObject ChongC1;
    public GameObject ChongC2;
    public GameObject ChongC3;
    public GameObject CheechC1;
    public GameObject CheechC2;
    public GameObject CheechC3;
    public Text letter1;
    public Text letter2;
    public Text letter3;
    public Text winPlayer;
    public GameObject LetterArrow;
    private String[] letter1charSet = { " ", "_", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "<", " " };
    private String[] letter2charSet = { " ", "_", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "<", " " };
    private String[] letter3charSet = { " ", "_", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "<", " " };
    private String[] basecharSet = { " ", "_", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "<", " " };
    public GameObject HisCorre;



    private Transform entryContainer;
    private Transform entryTemplate;
    private List<Transform> highscoreEntryTransformList;
    private int letterIndex = 1;
    private int maxValue = 0;
    private String winnerName = "";
    private Boolean letterInputBoolean = false;
    public int introSw = 1;
    public Boolean hscRefresh = false;

    // Start is called before the first frame update
    void Start()
    {
        commport = System.IO.File.ReadAllText(@"C:\Pinball\Commport.txt");
        Baudrate = int.Parse(System.IO.File.ReadAllText(@"C:\Pinball\Baudrate.txt"));
        mySPort = new SerialPort(commport, Baudrate);
        mySPort.Open();
        mySPort.ReadTimeout = 45;

        Final.gameObject.SetActive(false);
        Player2.gameObject.SetActive(false);
        Player3.gameObject.SetActive(false);
        Player4.gameObject.SetActive(false);
        Multiball1.gameObject.SetActive(false);
        Multiball2.gameObject.SetActive(false);
        Multiball3.gameObject.SetActive(false);
        Multiball4.gameObject.SetActive(false);
        Point1.gameObject.SetActive(false);
        Point2.gameObject.SetActive(false);
        Point3.gameObject.SetActive(false);
        Point4.gameObject.SetActive(false);
        Point5.gameObject.SetActive(false);
        Point6.gameObject.SetActive(false);
        Point7.gameObject.SetActive(false);
        Point8.gameObject.SetActive(false);
        weed.gameObject.SetActive(false);
        drift.gameObject.SetActive(false);
        StartScr.gameObject.SetActive(false);
        delej = System.Environment.TickCount;
        jackpot1.gameObject.SetActive(false);
        jackpot2.gameObject.SetActive(false);
        jackpot3.gameObject.SetActive(false);
        jackpot4.gameObject.SetActive(false);
        jackpot5.gameObject.SetActive(false);
        jackpot6.gameObject.SetActive(false);
        bonus1.gameObject.SetActive(false);
        bonus2.gameObject.SetActive(false);
        bonus3.gameObject.SetActive(false);
        bonus4.gameObject.SetActive(false);
        extraB.gameObject.SetActive(false);
        ufo1.gameObject.SetActive(false);
        ufo2.gameObject.SetActive(false);
        ufo3.gameObject.SetActive(false);
        ufo4.gameObject.SetActive(false);
        ufo5.gameObject.SetActive(false);
        ufo6.gameObject.SetActive(false);
        ufo7.gameObject.SetActive(false);
        ufo8.gameObject.SetActive(false);
        ufo9.gameObject.SetActive(false);
        ufo10.gameObject.SetActive(false);
        ufo11.gameObject.SetActive(false);
        ufo12.gameObject.SetActive(false);
        ufo13.gameObject.SetActive(false);
        beer1.gameObject.SetActive(false);
        beer2.gameObject.SetActive(false);
        beer3.gameObject.SetActive(false);
        combo1.gameObject.SetActive(false);
        combo2.gameObject.SetActive(false);
        combo3.gameObject.SetActive(false);
        combo4.gameObject.SetActive(false);
        combo5.gameObject.SetActive(false);
        combo6.gameObject.SetActive(false);
        danger.gameObject.SetActive(false);
        tilt.gameObject.SetActive(false);
        ChongC1.gameObject.SetActive(false);
        ChongC2.gameObject.SetActive(false);
        ChongC3.gameObject.SetActive(false);
        CheechC1.gameObject.SetActive(false);
        CheechC2.gameObject.SetActive(false);
        CheechC3.gameObject.SetActive(false);

    }

    // Update is called once per frame
    void Update()
    {
        if (gameState != 3)
        {
            if (mySPort.IsOpen)  //youtube tutorial's method 
            {
                try
                {

                    // do other stuff with the data
                    IncomeMsg = mySPort.ReadLine();
                }
                catch (TimeoutException e)
                {
                    // no-op, just to silence the timeouts. 
                    // (my arduino sends 12-16 byte packets every 0.1 secs)
                }
            }
        }






        if (gameState == 1)
        {
            HisCorre.gameObject.SetActive(true);
            switch (introSw)
            {
                case 0: // Intro
                    Cam.gameObject.transform.position = new Vector3(-2000, 240, -10);
                    IntroScr.gameObject.SetActive(true);
                    StartScr.gameObject.SetActive(false);
                    ThxScr.gameObject.SetActive(false);
                    break;
                case 1: // Press Start
                    Cam.gameObject.transform.position = new Vector3(-450, 240, -10);
                    StartScr.gameObject.SetActive(true);
                    ThxScr.gameObject.SetActive(false);
                    IntroScr.gameObject.SetActive(false);
                    break;
                case 3: // Hiscore
                    StartScr.gameObject.SetActive(false);
                    ThxScr.gameObject.SetActive(false);
                    Cam.gameObject.transform.position = new Vector3(360, 240, -10);
                    IntroScr.gameObject.SetActive(false);
                    break;
                case 4: // Press Start
                    Cam.gameObject.transform.position = new Vector3(-450, 240, -10);
                    StartScr.gameObject.SetActive(true);
                    ThxScr.gameObject.SetActive(false);
                    IntroScr.gameObject.SetActive(false);
                    break;
                case 5: // THX screen
                    Cam.gameObject.transform.position = new Vector3(-1250, 240, -10);
                    StartScr.gameObject.SetActive(false);
                    ThxScr.gameObject.SetActive(true);
                    IntroScr.gameObject.SetActive(false);
                    break;
                case 6: // Press Start
                    Cam.gameObject.transform.position = new Vector3(-450, 240, -10);
                    StartScr.gameObject.SetActive(true);
                    ThxScr.gameObject.SetActive(false);
                    IntroScr.gameObject.SetActive(false);
                    break;
                case 7: // Hiscore
                    StartScr.gameObject.SetActive(false);
                    ThxScr.gameObject.SetActive(false);
                    Cam.gameObject.transform.position = new Vector3(360, 240, -10);
                    IntroScr.gameObject.SetActive(false);
                    break;
                case 8: // Press Start
                    Cam.gameObject.transform.position = new Vector3(-450, 240, -10);
                    StartScr.gameObject.SetActive(true);
                    ThxScr.gameObject.SetActive(false);
                    IntroScr.gameObject.SetActive(false);
                    break;
            }
            if (System.Environment.TickCount - 8000 > delej)
            {
                introSw = introSw + 1;
                delej = System.Environment.TickCount;
                if (introSw == 9)
                {
                    introSw = 0;
                }
            }
            if (IncomeMsg == "Zero") /// EZT ÍRD ÁT BASZKI
            {
                gameState = 2;
                HisCorre.gameObject.SetActive(false);
                Cam.gameObject.transform.position = new Vector3(1200, 240, -10);
                StartScr.gameObject.SetActive(false);
                ThxScr.gameObject.SetActive(false);
                IntroScr.gameObject.SetActive(false);
            }
        }

        if (gameState == 2)
        {
            Cam.gameObject.transform.position = new Vector3(1200, 240, -10);
            if (IncomeMsg != "")
            {
                string[] lines = Regex.Split(IncomeMsg, ",");
                if (lines[0] == "score")
                {
                    Numofplayers = int.Parse(lines[2]);
                    Player = int.Parse(lines[3]);
                    Balln = int.Parse(lines[4]);
                    Score = int.Parse(lines[1]);
                    Bonus = int.Parse(lines[5]);
                    BonusMultiplier = int.Parse(lines[6]);
                    if (BonusMultiplier == 0)
                    {
                        BonusMultiplierNum = 1;
                    }
                    if (BonusMultiplier == 1)
                    {
                        BonusMultiplierNum = 2;
                    }
                    if (BonusMultiplier == 2)
                    {
                        BonusMultiplierNum = 4;
                    }
                    if (BonusMultiplier == 3)
                    {
                        BonusMultiplierNum = 6;
                    }
                    if (BonusMultiplier == 4)
                    {
                        BonusMultiplierNum = 8;
                    }


                    Scorearray[Player - 1] = Score;
                    ScoreText.text = Score.ToString();
                    Player1cstext.text = Scorearray[0].ToString();
                    Player2cstext.text = Scorearray[1].ToString();
                    Player3cstext.text = Scorearray[2].ToString();
                    Player4cstext.text = Scorearray[3].ToString();
                    BallNumText.text = "Ball " + Balln.ToString();
                    PlayerNumText.text = "Player " + Player.ToString();
                    EndGPlayer.text = "Player " + Player.ToString();
                    EndGScore.text = "Score : " + Score.ToString();
                    EndGBonus.text = "Bonus : " + Bonus.ToString();
                    EndGBonusM.text = "Bonus Multiplier : " + BonusMultiplierNum.ToString() + " X";

                    


                    if (Numofplayers != NumofPlayerPrevious)
                    {
                        switch (Numofplayers)
                        {
                            case 1:
                                Player2.gameObject.SetActive(false);
                                Player3.gameObject.SetActive(false);
                                Player4.gameObject.SetActive(false);
                                NumofPlayerPrevious = Numofplayers;
                                break;
                            case 2:
                                Player2.gameObject.SetActive(true);
                                Player3.gameObject.SetActive(false);
                                Player4.gameObject.SetActive(false);
                                NumofPlayerPrevious = Numofplayers;
                                break;
                            case 3:
                                Player2.gameObject.SetActive(true);
                                Player3.gameObject.SetActive(true);
                                Player4.gameObject.SetActive(false);
                                NumofPlayerPrevious = Numofplayers;
                                break;
                            case 4:
                                Player2.gameObject.SetActive(true);
                                Player3.gameObject.SetActive(true);
                                Player4.gameObject.SetActive(true);
                                NumofPlayerPrevious = Numofplayers;
                                break;
                        }
                    }
                }
                if (lines[0] == "Next")
                {
                    gameState = 3;
                    State3delay = System.Environment.TickCount;
                    lines[0] = "";
                    SumTotal = Scorearray[Player - 1] + (Bonus * BonusMultiplierNum);
                    Totalscore.text = SumTotal.ToString();
                    Final.gameObject.SetActive(true);
                }

                if (lines[0] == "End")
                {
                    Cam.gameObject.transform.position = new Vector3(1200, -600, -10);
                    gameState = 4;
                    State3delay = System.Environment.TickCount;
                    lines[0] = "";
                    SumTotal = Scorearray[Player - 1] + (Bonus * BonusMultiplierNum);
                    Totalscore.text = SumTotal.ToString();
                    Final.gameObject.SetActive(true);
                }


                if (lines[0] == "Multiball1" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Multiball1.gameObject.SetActive(true);
                    SceneoffHandler(1);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Multiball2" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Multiball2.gameObject.SetActive(true);
                    SceneoffHandler(2);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Multiball3" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Multiball3.gameObject.SetActive(true);
                    SceneoffHandler(3);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Multiball4" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Multiball4.gameObject.SetActive(true);
                    SceneoffHandler(4);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Point1" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Point1.gameObject.SetActive(true);
                    SceneoffHandler(5);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Point2" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Point2.gameObject.SetActive(true);
                    SceneoffHandler(6);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Point3" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Point3.gameObject.SetActive(true);
                    SceneoffHandler(7);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Point4" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Point4.gameObject.SetActive(true);
                    SceneoffHandler(8);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Point5" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Point5.gameObject.SetActive(true);
                    SceneoffHandler(9);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Point6" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Point6.gameObject.SetActive(true);
                    SceneoffHandler(10);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Point7" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Point7.gameObject.SetActive(true);
                    SceneoffHandler(11);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Point8" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    Point8.gameObject.SetActive(true);
                    SceneoffHandler(12);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Weed" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    weed.gameObject.SetActive(true);
                    SceneoffHandler(13);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Drift")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    drift.gameObject.SetActive(true);
                    SceneoffHandler(14);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Jackpot1" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    jackpot1.gameObject.SetActive(true);
                    SceneoffHandler(15);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Jackpot2" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    jackpot2.gameObject.SetActive(true);
                    SceneoffHandler(16);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Jackpot3" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    jackpot3.gameObject.SetActive(true);
                    SceneoffHandler(17);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Jackpot4" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    jackpot4.gameObject.SetActive(true);
                    SceneoffHandler(18);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Jackpot5" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    jackpot5.gameObject.SetActive(true);
                    SceneoffHandler(19);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Jackpot6" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    jackpot6.gameObject.SetActive(true);
                    SceneoffHandler(20);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Bonus1" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    bonus1.gameObject.SetActive(true);
                    SceneoffHandler(21);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Bonus2" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    bonus2.gameObject.SetActive(true);
                    SceneoffHandler(22);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Bonus3" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    bonus3.gameObject.SetActive(true);
                    SceneoffHandler(23);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Bonus4" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    bonus4.gameObject.SetActive(true);
                    SceneoffHandler(24);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "ExtraB" )
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    extraB.gameObject.SetActive(true);
                    SceneoffHandler(25);
                    Animhandler = 1;
                    lines[0] = "";
                }

                if (lines[0] == "Ufo1")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo1.gameObject.SetActive(true);
                    SceneoffHandler(26);
                    Animhandler = 2;
                    lines[0] = "";
                }

                if (lines[0] == "Ufo2")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo2.gameObject.SetActive(true);
                    SceneoffHandler(27);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo3")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo3.gameObject.SetActive(true);
                    SceneoffHandler(28);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo4")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo4.gameObject.SetActive(true);
                    SceneoffHandler(29);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo5")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo5.gameObject.SetActive(true);
                    SceneoffHandler(30);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Beer1")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    beer1.gameObject.SetActive(true);
                    SceneoffHandler(31);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Beer2")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    beer2.gameObject.SetActive(true);
                    SceneoffHandler(32);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Beer3")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    beer3.gameObject.SetActive(true);
                    SceneoffHandler(33);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo6")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo6.gameObject.SetActive(true);
                    SceneoffHandler(34);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Combo1")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    combo1.gameObject.SetActive(true);
                    SceneoffHandler(35);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Combo2")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    combo2.gameObject.SetActive(true);
                    SceneoffHandler(36);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Combo3")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    combo3.gameObject.SetActive(true);
                    SceneoffHandler(37);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Combo4")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    combo4.gameObject.SetActive(true);
                    SceneoffHandler(38);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Combo5")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    combo5.gameObject.SetActive(true);
                    SceneoffHandler(39);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Combo6")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    combo6.gameObject.SetActive(true);
                    SceneoffHandler(40);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo7")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo7.gameObject.SetActive(true);
                    SceneoffHandler(41);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo8")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo8.gameObject.SetActive(true);
                    SceneoffHandler(42);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo9")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo9.gameObject.SetActive(true);
                    SceneoffHandler(43);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Danger")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    danger.gameObject.SetActive(true);
                    SceneoffHandler(44);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Tilt")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    tilt.gameObject.SetActive(true);
                    SceneoffHandler(45);
                    Animhandler = 3;
                    lines[0] = "";
                }
                if (lines[0] == "ChongC1")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ChongC1.gameObject.SetActive(true);
                    SceneoffHandler(46);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "ChongC2")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ChongC2.gameObject.SetActive(true);
                    SceneoffHandler(47);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "ChongC3")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ChongC3.gameObject.SetActive(true);
                    SceneoffHandler(48);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "CheechC1")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    CheechC1.gameObject.SetActive(true);
                    SceneoffHandler(49);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "CheechC2")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    CheechC2.gameObject.SetActive(true);
                    SceneoffHandler(50);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "CheechC3")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    CheechC3.gameObject.SetActive(true);
                    SceneoffHandler(51);
                    Animhandler = 1;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo10")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo10.gameObject.SetActive(true);
                    SceneoffHandler(52);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo11")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo11.gameObject.SetActive(true);
                    SceneoffHandler(53);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo12")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo12.gameObject.SetActive(true);
                    SceneoffHandler(54);
                    Animhandler = 2;
                    lines[0] = "";
                }
                if (lines[0] == "Ufo13")
                {
                    AnimPlayOn = true;
                    CurrentTime = System.Environment.TickCount;
                    ufo13.gameObject.SetActive(true);
                    SceneoffHandler(55);
                    Animhandler = 2;
                    lines[0] = "";
                }

                if (System.Environment.TickCount - 4200 > CurrentTime & Animhandler == 1)
                {
                    SceneoffHandler(0);
                    Animhandler = 0;
                    AnimPlayOn = false;

                }
                if (System.Environment.TickCount - 7000 > CurrentTime & Animhandler == 2)
                {
                    SceneoffHandler(0);
                    Animhandler = 0;
                    AnimPlayOn = false;
                }
                if (System.Environment.TickCount - 10000 > CurrentTime & Animhandler == 3)
                {
                    SceneoffHandler(0);
                    Animhandler = 0;
                    AnimPlayOn = false;
                }

            }
        }






        if (gameState == 3)
        {
            SceneoffHandler(0);
            Cam.gameObject.transform.position = new Vector3(1200, -600, -10);
            if (System.Environment.TickCount - 4500 > State3delay)
            {
                    gameState = 2;
                    Scorearray[Player - 1] = Scorearray[Player - 1] + (Bonus * BonusMultiplierNum);
                    Player1cstext.text = Scorearray[0].ToString();
                    Player2cstext.text = Scorearray[1].ToString();
                    Player3cstext.text = Scorearray[2].ToString();
                    Player4cstext.text = Scorearray[3].ToString();
                    Bonus = 0;
                    BonusMultiplier = 0;
                    Final.gameObject.SetActive(false);
            }
        }

        if (gameState == 4)
        {
            SceneoffHandler(0);
            if (System.Environment.TickCount - 4500 > State3delay)
            {
                Scorearray[Player - 1] = Scorearray[Player - 1] + (Bonus * BonusMultiplierNum);
                Player1cstext.text = Scorearray[0].ToString();
                Player2cstext.text = Scorearray[1].ToString();
                Player3cstext.text = Scorearray[2].ToString();
                Player4cstext.text = Scorearray[3].ToString();
                Bonus = 0;
                BonusMultiplier = 0;
                Player1csWin.text = Player1cstext.text;
                Player2csWin.text = Player2cstext.text;
                Player3csWin.text = Player3cstext.text;
                Player4csWin.text = Player4cstext.text;
                Final.gameObject.SetActive(false);
                Cam.gameObject.transform.position = new Vector3(1200, -1200, -10);
                

            }

            if (System.Environment.TickCount - 10000 > State3delay)
            {
                maxValue = Scorearray.Max();
                // Debug.Log(maxValue);
                int maxIndex = Scorearray.ToList().IndexOf(maxValue);
                winPlayer.text = "Player " + (maxIndex + 1) + " Hiscore";

                string jsonString = PlayerPrefs.GetString("CnCHCT");
                Highscores highscores = JsonUtility.FromJson<Highscores>(jsonString);
                // Sort entry list by Score
                for (int i = 0; i < highscores.highscoreEntryList.Count; i++)
                {
                    for (int j = i + 1; j < highscores.highscoreEntryList.Count; j++)
                    {
                        if (highscores.highscoreEntryList[j].score > highscores.highscoreEntryList[i].score)
                        {
                            // Swap
                            HighscoreEntry tmp = highscores.highscoreEntryList[i];
                            highscores.highscoreEntryList[i] = highscores.highscoreEntryList[j];
                            highscores.highscoreEntryList[j] = tmp;
                        }
                    }
                }
                // Debug.Log(highscores.highscoreEntryList[9].score);
                if (highscores.highscoreEntryList[9].score > maxValue)
                {
                    gameState = 1;
                    Scorearray[0] = 0;
                    Scorearray[1] = 0;
                    Scorearray[2] = 0;
                    Scorearray[3] = 0;
                    Bonus = 0;
                    mySPort.Write("Exit");
                }
                else {
                    Cam.gameObject.transform.position = new Vector3(1200, -1800, -10);
                    gameState = 5;
                    letter1.text = letter1charSet[1];
                    letter2.text = letter2charSet[1];
                    letter3.text = letter3charSet[1];
                    letterIndex = 1;
                }
            }   
        }

        if (gameState == 5)
        {
            string[] lines = Regex.Split(IncomeMsg, ",");
            if (lines[0] == "Start")
            {
                gameState = 1;
                introSw = 1;
                delej = System.Environment.TickCount;
                Scorearray[0] = 0;
                Scorearray[1] = 0;
                Scorearray[2] = 0;
                Scorearray[3] = 0;
                Bonus = 0;
                letterIndex = 1;
                mySPort.Write("Exit1");
                for (int i = 0; i < 40; i++)
                {
                    letter1charSet[i] = basecharSet[i];
                    letter2charSet[i] = basecharSet[i];
                    letter3charSet[i] = basecharSet[i];
                }

            }

            if (System.Environment.TickCount - 300 > letterdelay)
            {
                letterInputBoolean = false;
            }
            switch (letterIndex)
            {
                case 1:
                    if (lines[0] == "Left" & letterInputBoolean == false)
                    {
                        letterInputBoolean = true;
                        letterdelay = System.Environment.TickCount;
                        lines[0] = " ";
                        for (int i = 0; i < 39; i++)
                        {
                            letter1charSet[i] = letter1charSet[i+1];
                            //Debug.Log(letter1charSet[i]);
                        }
                        letter1charSet[38] = letter1charSet[0];
                    }
                    if (lines[0] == "Right" & letterInputBoolean == false)
                    {
                        letterInputBoolean = true;
                        letterdelay = System.Environment.TickCount;
                        for (int i = 39; i > 1; i --)
                        {
                            letter1charSet[i] = letter1charSet[i - 1];
                            Debug.Log(letter1charSet[i]);
                        }
                        letter1charSet[1] = letter1charSet[39];
                    }
                    if (lines[0] == "Shoot" & letterInputBoolean == false)
                    {
                        letterInputBoolean = true;
                        letterdelay = System.Environment.TickCount;
                        letterIndex = 2;
                    }
                    LetterArrow.gameObject.transform.position = new Vector3(1140, -1840, 10);
                    letter1.text = letter1charSet[1];
                    break;

                case 2:
                    if (lines[0] == "Left" & letterInputBoolean == false)
                    {
                        letterInputBoolean = true;
                        letterdelay = System.Environment.TickCount;
                        lines[0] = " ";
                        for (int i = 0; i < 39; i++)
                        {
                            letter2charSet[i] = letter2charSet[i + 1];
                            //Debug.Log(letter1charSet[i]);
                        }
                        letter2charSet[38] = letter2charSet[0];
                    }
                    if (lines[0] == "Right" & letterInputBoolean == false)
                    {
                        letterInputBoolean = true;
                        letterdelay = System.Environment.TickCount;
                        for (int i = 39; i > 1; i--)
                        {
                            letter2charSet[i] = letter2charSet[i - 1];
                            Debug.Log(letter1charSet[i]);
                        }
                        letter2charSet[1] = letter2charSet[39];
                    }
                    if (lines[0] == "Shoot" & letterInputBoolean == false)
                    {
                        letterInputBoolean = true;
                        letterdelay = System.Environment.TickCount;
                        if (letter2charSet[1] == "<")
                        {
                            letterIndex = 1;
                        }
                        else
                        {
                            letterIndex = 3;
                        }
                    }
                    LetterArrow.gameObject.transform.position = new Vector3(1200, -1840, 10);
                    letter2.text = letter2charSet[1];
                    break;

                case 3:
                    if (lines[0] == "Left" & letterInputBoolean == false)
                    {
                        letterInputBoolean = true;
                        letterdelay = System.Environment.TickCount;
                        lines[0] = " ";
                        for (int i = 0; i < 39; i++)
                        {
                            letter3charSet[i] = letter3charSet[i + 1];
                            //Debug.Log(letter1charSet[i]);
                        }
                        letter3charSet[38] = letter3charSet[0];
                    }
                    if (lines[0] == "Right" & letterInputBoolean == false)
                    {
                        letterInputBoolean = true;
                        letterdelay = System.Environment.TickCount;
                        for (int i = 39; i > 1; i--)
                        {
                            letter3charSet[i] = letter3charSet[i - 1];
                            Debug.Log(letter1charSet[i]);
                        }
                        letter3charSet[1] = letter3charSet[39];
                    }
                    if (lines[0] == "Shoot" & letterInputBoolean == false)
                    {
                        if (letter3charSet[1] == "<")
                        {
                            letterInputBoolean = true;
                            letterdelay = System.Environment.TickCount;
                            letterIndex = 2;
                        }
                        else
                        {
                            winnerName = letter1charSet[1] + letter2charSet[1] + letter3charSet[1];
                            AddHighscoreEntry(maxValue, winnerName);
                            gameState = 1;
                            introSw = 3;
                            hscRefresh = true;
                            delej = System.Environment.TickCount;
                            for (int i = 0; i < 40; i++)
                            {
                                letter1charSet[i] = basecharSet[i];
                                letter2charSet[i] = basecharSet[i];
                                letter3charSet[i] = basecharSet[i];
                            }
                            Scorearray[0] = 0;
                            Scorearray[1] = 0;
                            Scorearray[2] = 0;
                            Scorearray[3] = 0;
                            Bonus = 0;
                            letterIndex = 1;
                            mySPort.Write("Exit2");

                        }
                    }
                    LetterArrow.gameObject.transform.position = new Vector3(1260, -1840, 10);
                    letter3.text = letter3charSet[1];
                    break;


            }



        }







        if (Input.GetKey("escape"))
        {
            Application.Quit();
        }
    }
    private void AddHighscoreEntry(int score, string name)
    {
        // Create HighscoreEntry
        HighscoreEntry highscoreEntry = new HighscoreEntry { score = score, name = name };

        // Load saved Highscores
        string jsonString = PlayerPrefs.GetString("CnCHCT");
        Highscores highscores = JsonUtility.FromJson<Highscores>(jsonString);

        if (highscores == null)
        {
            // There's no stored table, initialize
            highscores = new Highscores()
            {
                highscoreEntryList = new List<HighscoreEntry>()
            };
        }

        // Add new entry to Highscores
        highscores.highscoreEntryList.Add(highscoreEntry);

        if (highscores != null)
        {
            for (int i = 0; i < highscores.highscoreEntryList.Count; i++)
            {
                for (int j = i + 1; j < highscores.highscoreEntryList.Count; j++)
                {
                    if (highscores.highscoreEntryList[j].score > highscores.highscoreEntryList[i].score)
                    {
                        // Swap
                        HighscoreEntry tmp = highscores.highscoreEntryList[i];
                        highscores.highscoreEntryList[i] = highscores.highscoreEntryList[j];
                        highscores.highscoreEntryList[j] = tmp;
                    }
                }
            }
            if (highscores.highscoreEntryList.Count > 9)
            {
                highscores.highscoreEntryList.RemoveAt(10);
            }
        }

        // Save updated Highscores
        string json = JsonUtility.ToJson(highscores);
        PlayerPrefs.SetString("CnCHCT", json);
        PlayerPrefs.Save();
    }



    void SceneoffHandler(int AnimID)
    {
        if (AnimID != 1)
        {
            Multiball1.gameObject.SetActive(false);

        }
        if (AnimID != 2)
        {
            Multiball2.gameObject.SetActive(false);

        }
        if (AnimID != 3)
        {
            Multiball3.gameObject.SetActive(false);

        }
        if (AnimID != 4)
        {
            Multiball4.gameObject.SetActive(false);

        }
        if (AnimID != 5)
        {
            Point1.gameObject.SetActive(false);
        }
        if (AnimID != 6)
        {
            Point2.gameObject.SetActive(false);
        }
        if (AnimID != 7)
        {
            Point3.gameObject.SetActive(false);
        }
        if (AnimID != 8)
        {
            Point4.gameObject.SetActive(false);
        }
        if (AnimID != 9)
        {
            Point5.gameObject.SetActive(false);
        }
        if (AnimID != 10)
        {
            Point6.gameObject.SetActive(false);
        }
        if (AnimID != 11)
        {
            Point7.gameObject.SetActive(false);
        }
        if (AnimID != 12)
        {
            Point8.gameObject.SetActive(false);
        }
        if (AnimID != 13)
        {
            weed.gameObject.SetActive(false);
        }
        if (AnimID != 14)
        {
            drift.gameObject.SetActive(false);
        }
        if (AnimID != 15)
        {
            jackpot1.gameObject.SetActive(false);
        }
        if (AnimID != 16)
        {
            jackpot2.gameObject.SetActive(false);
        }
        if (AnimID != 17)
        {
            jackpot3.gameObject.SetActive(false);
        }
        if (AnimID != 18)
        {
            jackpot4.gameObject.SetActive(false);
        }
        if (AnimID != 19)
        {
            jackpot5.gameObject.SetActive(false);
        }
        if (AnimID != 20)
        {
            jackpot6.gameObject.SetActive(false);
        }
        if (AnimID != 21)
        {
            bonus1.gameObject.SetActive(false);
        }
        if (AnimID != 22)
        {
            bonus2.gameObject.SetActive(false);
        }
        if (AnimID != 23)
        {
            bonus3.gameObject.SetActive(false);
        }
        if (AnimID != 24)
        {
            bonus4.gameObject.SetActive(false);
        }
        if (AnimID != 25)
        {
            extraB.gameObject.SetActive(false);
        }
        if (AnimID != 26)
        {
            ufo1.gameObject.SetActive(false);
        }
        if (AnimID != 27)
        {
            ufo2.gameObject.SetActive(false);
        }
        if (AnimID != 28)
        {
            ufo3.gameObject.SetActive(false);
        }
        if (AnimID != 29)
        {
            ufo4.gameObject.SetActive(false);
        }
        if (AnimID != 30)
        {
            ufo5.gameObject.SetActive(false);
        }
        if (AnimID != 31)
        {
            beer1.gameObject.SetActive(false);
        }
        if (AnimID != 32)
        {
            beer2.gameObject.SetActive(false);
        }
        if (AnimID != 33)
        {
            beer3.gameObject.SetActive(false);
        }
        if (AnimID != 34)
        {
            ufo6.gameObject.SetActive(false);
        }
        if (AnimID != 35)
        {
            combo1.gameObject.SetActive(false);
        }
        if (AnimID != 36)
        {
            combo2.gameObject.SetActive(false);
        }
        if (AnimID != 37)
        {
            combo3.gameObject.SetActive(false);
        }
        if (AnimID != 38)
        {
            combo4.gameObject.SetActive(false);
        }
        if (AnimID != 39)
        {
            combo5.gameObject.SetActive(false);
        }
        if (AnimID != 40)
        {
            combo6.gameObject.SetActive(false);
        }
        if (AnimID != 41)
        {
            ufo7.gameObject.SetActive(false);
        }
        if (AnimID != 42)
        {
            ufo8.gameObject.SetActive(false);
        }
        if (AnimID != 43)
        {
            ufo9.gameObject.SetActive(false);
        }
        if (AnimID != 44)
        {
            danger.gameObject.SetActive(false);
        }
        if (AnimID != 45)
        {
            tilt.gameObject.SetActive(false);
        }
        if (AnimID != 46)
        {
            ChongC1.gameObject.SetActive(false);
        }
        if (AnimID != 47)
        {
            ChongC2.gameObject.SetActive(false);
        }
        if (AnimID != 48)
        {
            ChongC3.gameObject.SetActive(false);
        }
        if (AnimID != 49)
        {
            CheechC1.gameObject.SetActive(false);
        }
        if (AnimID != 50)
        {
            CheechC2.gameObject.SetActive(false);
        }
        if (AnimID != 51)
        {
            CheechC3.gameObject.SetActive(false);
        }
        if (AnimID != 52)
        {
            ufo10.gameObject.SetActive(false);
        }
        if (AnimID != 53)
        {
            ufo11.gameObject.SetActive(false);
        }
        if (AnimID != 54)
        {
            ufo12.gameObject.SetActive(false);
        }
        if (AnimID != 55)
        {
            ufo13.gameObject.SetActive(false);
        }


    }
    private class Highscores
    {
        public List<HighscoreEntry> highscoreEntryList;
    }

    /*
     * Represents a single High score entry
     * */
    [System.Serializable]
    private class HighscoreEntry
    {
        public int score;
        public string name;
    }
}


